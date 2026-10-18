"""Screen 2 - Inbox Review. A person approves, edits, rejects or escalates every AI draft."""

import json
import os

import pandas as pd
import streamlit as st

from common.kb_client import KnowledgeClient
from task2_inbox_agent import dataset
from task2_inbox_agent.agent import build_agent
from task2_inbox_agent.autonomy import THRESHOLD

CATEGORY_LABEL = {"how_to": "How-to", "troubleshooting": "Troubleshooting", "project_request": "Project request",
                  "refund_cancellation": "Refund / cancel", "complaint": "Complaint", "quote_request": "Quote",
                  "follow_up": "Follow-up", "other": "Other"}


@st.cache_resource
def agent():
    return build_agent(kb=KnowledgeClient(requester="inbox-agent"))


st.title("Inbox Review")
st.caption("Every reply is drafted from the help center and checked by safety rules. Nothing is sent without a "
           "person, unless a category has earned autonomy. Sending is simulated (no real emails).")
if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
    st.error("ANTHROPIC_API_KEY is not set, so new emails cannot be processed. Add it to .env and restart.")
a = agent()
store = a.store

with st.sidebar:
    st.subheader("Incoming emails")
    data = dataset.load("dev") + dataset.load("holdout")
    done = {r[0] for r in store.conn.execute("SELECT id FROM emails")}
    waiting = [e for e in data if e.email.id not in done]
    st.caption(f"{len(waiting)} sample emails not yet processed")
    n = st.number_input("How many to process", 1, 20, 3)
    if st.button("Process next emails", disabled=not waiting):
        bar = st.progress(0.0)
        for i, item in enumerate(waiting[:n]):
            a.process(item.email)
            bar.progress((i + 1) / min(n, len(waiting)))
        st.rerun()
    st.divider()
    st.subheader("Autonomy")
    enabled = st.toggle("Allow autonomous sending", value=a.autonomy.enabled)
    if enabled != a.autonomy.enabled:
        a.autonomy.set_enabled(enabled, actor="ui")
        st.rerun()

review_tab, autonomy_tab, history_tab, learning_tab = st.tabs(["Review queue", "Autonomy", "History", "Learning"])

with review_tab:
    rows = store.conn.execute(
        """SELECT i.id, i.kind, i.decision, i.decision_reason, e.subject, e.from_name, e.id AS email_id
           FROM interactions i JOIN emails e ON e.id = i.email_id
           WHERE i.outcome = 'pending' ORDER BY i.id""").fetchall()
    if not rows:
        st.info("Queue is empty. Process some emails from the sidebar.")
    else:
        labels = {r["id"]: f"#{r['id']} · {CATEGORY_LABEL.get(r['kind'], r['kind'] or '?')} · {r['subject'][:50]}"
                  + (" · AUDIT" if r["decision"] == "held_for_audit" else "")
                  + (" · KB DOWN" if r["decision"] == "kb_unavailable" else "") for r in rows}
        iid = st.selectbox("Pick an email", list(labels), format_func=labels.get)
        row = store.interaction(iid)
        cls = json.loads(row["classification"]) if row["classification"] else {}
        left, right = st.columns(2)
        with left:
            st.markdown(f"**From:** {row['from_name']} <{row['from_email']}>  \n**Subject:** {row['subject']}")
            st.text(row["body"])
            if cls:
                st.markdown(f"**Category:** {CATEGORY_LABEL.get(cls['category'])} · **Risk:** {cls['risk']} · "
                            f"**Urgency:** {cls['urgency']}" + (f" · **Flags:** {', '.join(cls['flags'])}"
                                                                 if cls["flags"] else ""))
                st.caption(cls["summary"])
            st.markdown(f"**Why it is here:** {row['decision_reason']}")
            if row["kb_response"]:
                kb = json.loads(row["kb_response"])
                with st.expander(f"Help-center evidence ({kb['status']})", expanded=False):
                    st.write(kb["answer"])
                    for s in kb["sources"]:
                        st.markdown(f"- [{s['title']}]({s['url']})")
            violations = json.loads(row["violations"])
            if violations:
                st.error("Safety check failed: " + ", ".join(violations) + ". Edit before sending.")
        with right:
            if row["decision"] == "kb_unavailable":
                st.error("Knowledge service was unavailable, so no draft was written (to avoid inventing facts). "
                         "Write a reply yourself or escalate.")
            text = st.text_area("Reply (edit freely)", value=row["draft_text"] or "", height=320, key=f"t{iid}")
            reviewer = st.text_input("Your name", value="reviewer", key=f"r{iid}")
            reason = st.text_input("Reason for change (helps the agent learn)", key=f"why{iid}")
            c1, c2, c3 = st.columns(3)
            try:
                if c1.button("Send", type="primary", key=f"s{iid}"):
                    out = a.send_reviewed(iid, text, reviewer, reason)
                    st.toast(f"Sent ({out['outcome'].replace('_', ' ')}). Streak for {row['kind']}: "
                             f"{out['autonomy']['streak']}/{THRESHOLD}")
                    st.rerun()
                if c2.button("Reject & send my text", key=f"x{iid}"):
                    a.reject(iid, text, reviewer, reason or "rejected")
                    st.rerun()
                if c3.button("Escalate to expert", key=f"e{iid}"):
                    out = a.escalate(iid, reviewer, reason or "needs expert")
                    st.toast(f"Escalated (Knowledge Brain task #{out['kb_escalation_id']})")
                    st.rerun()
            except ValueError as exc:
                st.error(str(exc))

with autonomy_tab:
    st.write(f"A category earns autonomy after **{THRESHOLD} replies in a row sent without any change**. "
             "Money, complaints, quotes and project requests never go out alone. Every 10th autonomous reply "
             "is held for an audit; any edit revokes autonomy.")
    states = a.autonomy.all_states()
    if states:
        df = pd.DataFrame(states)[["kind", "status", "streak", "auto_sent", "updated_at"]]
        st.dataframe(df, hide_index=True, width="stretch")
        kind = st.selectbox("Revoke autonomy for", [s["kind"] for s in states if s["status"] == "autonomous"] or ["-"])
        if kind != "-" and st.button("Revoke"):
            a.autonomy.revoke(kind, "ui", "manual revoke from Inbox Review")
            st.rerun()
    else:
        st.info("No reviewed emails yet.")

with history_tab:
    hist = pd.read_sql_query(
        """SELECT i.id, e.subject, i.kind, i.decision, i.outcome, i.similarity, i.reviewer, i.correction_reason,
                  i.cost_usd, i.latency_ms, i.prompt_versions
           FROM interactions i JOIN emails e ON e.id = i.email_id ORDER BY i.id DESC""", store.conn)
    st.dataframe(hist, hide_index=True, width="stretch")
    st.markdown("**Audit log**")
    st.dataframe(pd.read_sql_query("SELECT ts, actor, action, kind, interaction_id, details FROM audit_log "
                                   "ORDER BY id DESC LIMIT 200", store.conn), hide_index=True, width="stretch")

with learning_tab:
    p = a.prompts
    st.write("The drafter prompt is versioned. The agent can study human edits, propose a new version, and test it "
             "on a fixed set of emails before it goes live. You can roll back at any time.")
    versions = p.versions("drafter")
    st.markdown(f"Active drafter prompt: **{p.active_version('drafter')}** · available: {', '.join(versions)}")
    with st.expander("Show active prompt"):
        st.code(p.get("drafter").system)
    back = st.selectbox("Switch active version (rollback)", versions, index=versions.index(p.active_version("drafter")))
    if back != p.active_version("drafter") and st.button("Activate selected version"):
        from task2_inbox_agent.learning import rollback

        rollback(p, back, store, "ui")
        st.rerun()
    st.caption("To propose and test a new version from recorded edits, run: "
               "`python -m task2_inbox_agent.evals.run_learning`")
