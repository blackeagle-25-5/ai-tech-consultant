"""Screen 1 - Knowledge Chat. Talks to the Knowledge Brain only through its HTTP API."""

import streamlit as st

from common.kb_client import KnowledgeClient, KnowledgeUnavailable


@st.cache_resource
def client() -> KnowledgeClient:
    return KnowledgeClient(requester="knowledge-chat-ui", retries=0)


kb = client()
st.title("Knowledge Chat")
st.caption("Answers come only from the Brilliant Directories help center, with sources. "
           "If the help center does not cover a question, it goes to a human expert.")

try:
    health = kb.health()
    st.success(f"Knowledge service online: {health['articles_active']} articles, "
               f"{health['chunks_active']} searchable passages."
               + ("" if health["llm_configured"] else "  No model key configured: answers are disabled."))
    if health.get("llm_provider") == "groq":
        st.info("Model provider: Groq (open models). Set ANTHROPIC_API_KEY to switch to Claude.")
except KnowledgeUnavailable:
    st.error("Knowledge service unavailable. Start it with `python run.py` and try again.")
    st.stop()

ask, experts, ingestion = st.tabs(["Ask a question", "Expert queue", "Help-center sync"])

with ask:
    with st.form("ask"):
        question = st.text_area("Your question", placeholder="How do I set up a payment gateway?", height=80)
        model = st.radio("Model", ["haiku", "sonnet"], horizontal=True,
                         help="Haiku is cheaper and faster; see EVALS.md for the measured difference.")
        submitted = st.form_submit_button("Ask", type="primary")
    if submitted and question.strip():
        with st.spinner("Searching the help center..."):
            try:
                ans = kb.query(question.strip(), model=model)
            except KnowledgeUnavailable as exc:
                st.error(f"Could not get an answer: {exc}")
                st.stop()
        if ans.status == "answered":
            st.markdown(f"**Answer** · confidence: `{ans.confidence}`")
        else:
            st.warning("The help center does not clearly answer this. "
                       + (f"Sent to a human expert (task #{ans.escalation_id})." if ans.escalation_id else ""))
        st.write(ans.answer)
        if ans.sources:
            st.markdown("**Sources**")
            for s in ans.sources:
                kind = {"image": "screenshot", "expert": "expert answer"}.get(s.source_type, "article")
                st.markdown(f"- [{s.title}]({s.url}) · {kind} · relevance {s.relevance:.2f}"
                            + (f" · section *{s.section}*" if s.section else ""))
                if s.image_urls:
                    st.image(s.image_urls[:2], width=320)
        st.caption(f"{ans.latency_ms} ms · ${ans.cost_usd:.5f} · model {ans.model or 'none (no model call)'}")

with experts:
    st.write("Questions the bot could not answer. An expert's **general** answer becomes searchable knowledge; "
             "a **customer-specific** answer is stored but never reused.")
    status = st.radio("Show", ["open", "resolved"], horizontal=True)
    try:
        items = kb.list_escalations(status)
    except KnowledgeUnavailable as exc:
        st.error(str(exc))
        items = []
    if not items:
        st.info("Nothing here.")
    for e in items:
        with st.expander(f"#{e['id']} · {e['question'][:90]}"):
            st.caption(f"Reason: {e['reason']} · from {e['requester'] or 'unknown'} · {e['created_at'][:16]}")
            if e["evidence"]:
                st.markdown("Closest passages found: " + ", ".join(f"[{x['title']}]({x['url']})" for x in e["evidence"]))
            if e["status"] == "resolved":
                st.success(e.get("answer") or "")
                continue
            with st.form(f"resolve-{e['id']}"):
                answer = st.text_area("Expert answer")
                expert = st.text_input("Your name")
                scope = st.selectbox("Applies to", ["general", "customer_specific"])
                source_url = st.text_input("Supporting link (optional)")
                expires = st.date_input("Expires (optional)", value=None)
                if st.form_submit_button("Save answer"):
                    try:
                        kb.resolve_escalation(e["id"], answer, expert, scope, source_url or None,
                                              expires.isoformat() if expires else None)
                        st.success("Saved. General answers are searchable immediately.")
                        st.rerun()
                    except KnowledgeUnavailable as exc:
                        st.error(str(exc))

with ingestion:
    st.write("The help center is checked daily (03:00 UTC). Only new, changed or removed articles are processed.")
    try:
        s = kb.ingestion_status()
        cols = st.columns(4)
        cols[0].metric("Active articles", s["articles_active"])
        cols[1].metric("Searchable passages", s["chunks_active"])
        cols[2].metric("Screenshots described", s["images"].get("done", 0))
        cols[3].metric("Pages pending/failed", s["article_pages"].get("pending", 0) + s["article_pages"].get("failed", 0))
        if s["last_run"]:
            r = s["last_run"]
            st.markdown(f"**Last run #{r['id']}** ({r['mode']}, {r['status']}, started {r['started_at'][:16]}): "
                        f"added {r['added']}, updated {r['updated']}, removed {r['removed']}, "
                        f"unchanged {r['unchanged']}, failed {r['failed']}, screenshots {r['images_described']}. "
                        f"{r['message'] or ''}")
        if s["running"]:
            st.info("A sync is running now.")
        elif st.button("Run incremental sync now"):
            kb.run_ingestion("incremental")
            st.success("Sync started. Refresh this tab to watch progress.")
    except KnowledgeUnavailable as exc:
        st.error(str(exc))
