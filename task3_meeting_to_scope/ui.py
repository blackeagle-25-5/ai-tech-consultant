"""Screen 3 - Scope Builder. Transcript in; requirements, feasibility, estimate, proposal and changes out.
Nothing can be sent to the client until a person approves the version."""


import pandas as pd
import streamlit as st

from common.kb_client import KnowledgeClient
from common.llm import llm_configured, provider
from task3_meeting_to_scope import transcript as tx
from task3_meeting_to_scope.orchestrator import build_pipeline
from task3_meeting_to_scope.store import NotApproved

FEAS = {"existing_configuration": "✅ Existing setting", "existing_feature_with_limitation": "🟡 Existing, with limits",
        "custom_development": "🛠 Custom build", "needs_clarification": "❓ To be confirmed"}


@st.cache_resource
def pipeline():
    return build_pipeline(kb=KnowledgeClient(requester="scope-builder"))


st.title("Scope Builder")
st.caption("Turns client meeting transcripts into a scope of work, an estimate and a proposal. "
           "Product features are checked against the Knowledge Brain before anything is quoted as custom work.")
if not llm_configured():
    st.error("No model key set (ANTHROPIC_API_KEY, or GROQ_API_KEY for the dev fallback), so transcripts cannot be analysed. "
             "Add it to .env and restart.")
elif provider() == "groq":
    st.warning("Running on the TEMPORARY Groq fallback, not Claude. Final results and the demo must use Claude.")
p = pipeline()
store = p.store
meta = tx.load_meta()["projects"]

with st.sidebar:
    project_id = st.selectbox("Project", list(meta), format_func=lambda k: meta[k]["client"])
    project = meta[project_id]
    meetings = project["meetings"]
    st.subheader("Run analysis")
    initial = [m for m in meetings if m["kind"] == "initial"]
    followups = [m for m in meetings if m["kind"] == "follow_up"]
    uploaded = st.file_uploader("Or upload a transcript (.txt, 'Speaker: text' lines)", type=["txt"])
    if st.button("Analyse first meeting", type="primary"):
        with st.spinner("Five agents at work (about a minute)..."):
            if uploaded:
                ts = [tx.parse(uploaded.name.rsplit(".", 1)[0], uploaded.read().decode("utf-8"))]
            else:
                ts = [tx.load(m["id"]) for m in initial]
            p.run_initial(project_id, project["client"], ts)
        st.rerun()
    if followups and st.button("Compare follow-up meeting"):
        try:
            with st.spinner("Comparing with the approved baseline..."):
                p.run_followup(project_id, project["client"], tx.load(followups[0]["id"]))
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

versions = store.versions(project_id)
if not versions:
    st.info("No analysis yet. Use **Analyse first meeting** in the sidebar.")
    st.stop()

labels = {v["id"]: f"v{v['number']} · {v['status'].upper()} · {v['created_at'][:16]}"
          + (" · revision of approved baseline" if v["based_on"] else "") for v in versions}
vid = st.selectbox("Version", list(labels), index=len(labels) - 1, format_func=labels.get)
v = store.version(vid)
pl = v["payload"]
if pl.get("placeholder_transcripts"):
    st.warning("This version was built from PLACEHOLDER transcripts, not real recorded meetings.")
blockers = [f for f in pl["review"]["findings"] if f["severity"] == "blocker"]
stats = pl["stats"]
st.caption(f"Cost ${stats['cost_usd']:.4f} · stage times {stats['seconds']} · models {pl['models']}")

tabs = st.tabs(["Requirements", "Feasibility", "Scope", "Estimate", "Proposal", "Changes", "Approval"])

with tabs[0]:
    brief = pl["brief"]
    st.write(brief["summary"])
    st.markdown(f"**Budget:** {brief['budget'] or 'not stated'} · **Deadline:** {brief['deadline'] or 'not stated'}")
    rows = [{"id": r["id"], "requirement": r["title"], "priority": r["priority"], "status": r["status"],
             "origin": r["origin"],
             "source": "; ".join(f"{s['meeting_id']} L{s['line']}: \"{s['quote']}\"" + ("" if s["verified"] else " ⚠ unverified")
                                 for s in r["sources"])} for r in brief["requirements"]]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    c1, c2 = st.columns(2)
    c1.markdown("**Open questions**\n" + "\n".join(f"- {q['text']}" for q in brief["open_questions"]))
    c2.markdown("**Client commitments**\n" + "\n".join(f"- {q['text']}" for q in brief["client_commitments"]))

with tabs[1]:
    st.caption("Checked through the Knowledge Brain API. 'To be confirmed' means the help center had no clear evidence; "
               "that does not prove the feature is missing.")
    reqs = {r["id"]: r["title"] for r in pl["brief"]["requirements"]}
    st.dataframe(pd.DataFrame([{"id": f["req_id"], "requirement": reqs.get(f["req_id"], ""),
                                "result": FEAS[f["classification"]], "why": f["explanation"],
                                "sources": ", ".join(s["url"] for s in f["sources"])} for f in pl["feasibility"]]),
                 hide_index=True, width="stretch")

with tabs[2]:
    sow = pl["scope"]
    st.markdown(f"**Objective:** {sow['objective']}  \n**Current problem:** {sow['current_problem']}")
    for d in sow["deliverables"]:
        with st.expander(f"{d['id']}. {d['title']}" + (" (optional)" if d["optional"] else "") + f" · {', '.join(d['req_ids'])}"):
            st.write(d["solution"])
            st.markdown("\n".join(f"- {c}" for c in d["acceptance_criteria"]))
    for title, key in (("Exclusions", "exclusions"), ("Dependencies", "dependencies"),
                       ("Client responsibilities", "client_responsibilities"), ("Risks", "risks")):
        st.markdown(f"**{title}**\n" + "\n".join(f"- {x}" for x in sow[key]))

with tabs[3]:
    est = pl["estimate"]
    st.dataframe(pd.DataFrame(pl["estimate_tasks"]), hide_index=True, width="stretch")
    c = st.columns(4)
    c[0].metric("Low / High hours", f"{est['low']:g} / {est['high']:g}")
    c[1].metric("Expected hours (PERT)", f"{est['expected']:g}")
    c[2].metric("With contingency", f"{est['with_contingency']:g}")
    c[3].metric("Draft price", f"{est['currency']} {est['price_before_tax']:,.0f}")
    st.caption("Hours from the Estimation agent; totals, contingency and price computed in code from pricing.yaml. "
               f"Optional deliverables excluded from the total: {', '.join(est['optional_deliverables']) or 'none'}.")

with tabs[4]:
    for f in pl["review"]["findings"]:
        (st.error if f["severity"] == "blocker" else st.warning)(f"{f['severity'].title()}: {f['issue']}")
    st.markdown(pl["proposal_markdown"])

with tabs[5]:
    cr = pl.get("change_report")
    if not cr:
        st.info("This is a first version. Changes appear after a follow-up meeting is compared to an approved baseline.")
    else:
        base = store.version(v["based_on"])
        st.markdown(f"Compared with **approved v{base['number']}** (approved by {base['reviewer']}).")
        if cr["deadline_change"]:
            st.warning(f"Deadline changed: {base['payload']['brief']['deadline']} → {cr['deadline_change']}")
        if cr["budget_change"]:
            st.warning(f"Budget changed: {cr['budget_change']}")
        st.markdown("**Added**\n" + "\n".join(f"- {x['id']} {x['title']}" for x in cr["added"]) or "-")
        st.markdown("**Modified**\n" + "\n".join(f"- {x['id']} {x['title']}: {x['change']}" for x in cr["modified"]))
        st.markdown("**Removed**\n" + "\n".join(f"- {x['id']} {x['title']} ({x['reason']})" for x in cr["removed"]))
        st.markdown("**Unchanged:** " + ", ".join(cr["unchanged"]))
        st.error("**Out of scope - quote separately**\n" + "\n".join(f"- {x['id']} {x['title']}" for x in cr["out_of_scope"]))
        d = cr["hours_delta"]
        st.markdown(f"**Estimate change:** expected {d['expected']:+g} h, price {d['price_before_tax']:+,.0f}")

with tabs[6]:
    st.markdown(f"Status: **{v['status']}**" + (f" by {v['reviewer']} at {v['reviewed_at'][:16]}" if v["reviewer"] else ""))
    if v["status"] == "draft":
        reviewer = st.text_input("Reviewer name")
        note = st.text_area("Note" + (" (required: this draft has blockers)" if blockers else ""))
        c1, c2 = st.columns(2)
        if c1.button("Approve", type="primary"):
            try:
                store.approve(vid, reviewer, note)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        if c2.button("Reject"):
            store.reject(vid, reviewer or "reviewer", note or "rejected")
            st.rerun()
    if st.button("Send proposal to client (simulated)"):
        try:
            store.deliver(vid, "ui")
            st.success("Delivered (simulated). Recorded in the audit log.")
        except NotApproved as exc:
            st.error(f"Blocked: {exc}")
    st.markdown("**Audit log**")
    st.dataframe(pd.read_sql_query("SELECT ts, actor, action, version_id, details FROM audit_log WHERE project_id = ? "
                                   "ORDER BY id DESC", store.conn, params=(project_id,)),
                 hide_index=True, width="stretch")
