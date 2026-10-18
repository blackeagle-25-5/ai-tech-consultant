import pytest

from task3_meeting_to_scope.store import NotApproved

from .conftest import BASE_REQS, MEETING_A, MEETING_C, Responder, meeting, req


def run_a(pipeline):
    return pipeline.run_initial("fit", "FitFinder", [meeting("meeting_a", MEETING_A)])


def test_initial_run_produces_scope_estimate_and_proposal(pipeline_factory):
    p = pipeline_factory()
    v = p.store.version(run_a(p))
    payload = v["payload"]
    assert v["status"] == "draft"
    assert [r["id"] for r in payload["brief"]["requirements"]] == ["REQ-001", "REQ-002", "REQ-003"]
    assert all(s["verified"] for r in payload["brief"]["requirements"] for s in r["sources"])
    # 3 deliverables x (2 + 4*4 + 6)/6 = 4h expected each -> 12h, +10% = 13.2h, x $50 = $660
    est = payload["estimate"]
    assert est["expected"] == 12 and est["with_contingency"] == 13.2 and est["price_before_tax"] == 660
    assert "USD 660.00" in payload["proposal_markdown"]
    assert payload["prompt_versions"]["scope"] == "v1"


def test_missing_kb_evidence_is_not_proof_of_absence(pipeline_factory):
    p = pipeline_factory(kb_status="insufficient_evidence")
    feas = {f["req_id"]: f["classification"] for f in p.store.version(run_a(p))["payload"]["feasibility"]}
    # The model said "custom_development"/"existing" but the KB had no evidence -> needs clarification.
    assert set(feas.values()) == {"needs_clarification"}


def test_kb_unavailable_is_flagged_not_guessed(pipeline_factory):
    p = pipeline_factory(kb_fail=True)
    feas = p.store.version(run_a(p))["payload"]["feasibility"]
    assert all(f["kb_status"] == "unavailable" and f["classification"] == "needs_clarification" for f in feas)


def test_invented_quote_downgrades_requirement(pipeline_factory):
    invented = req("Native mobile app", 2, "build us an iPhone app with payments")
    p = pipeline_factory(Responder(BASE_REQS + [invented]))
    reqs = p.store.version(run_a(p))["payload"]["brief"]["requirements"]
    app = next(r for r in reqs if r["title"] == "Native mobile app")
    assert app["status"] == "unclear" and not app["sources"][0]["verified"]


def test_money_in_narrative_is_a_blocker_and_approval_needs_a_note(pipeline_factory):
    responder = Responder()
    responder.narrative_text = "All of this for just $999!"
    p = pipeline_factory(responder)
    vid = run_a(p)
    findings = p.store.version(vid)["payload"]["review"]["findings"]
    assert any(f["severity"] == "blocker" and "$999" in f["issue"] for f in findings)
    with pytest.raises(ValueError):
        p.store.approve(vid, "senior")
    p.store.approve(vid, "senior", note="Price sentence removed manually before sending")
    assert p.store.version(vid)["status"] == "approved"


def test_nothing_reaches_the_client_without_approval(pipeline_factory):
    p = pipeline_factory()
    vid = run_a(p)
    with pytest.raises(NotApproved):
        p.store.deliver(vid, "consultant")
    p.store.approve(vid, "senior")
    assert "Proposal for FitFinder" in p.store.deliver(vid, "consultant")
    actions = [r[0] for r in p.store.conn.execute("SELECT action FROM audit_log")]
    assert actions.count("delivery_blocked") == 1 and "delivered" in actions


def test_followup_requires_an_approved_baseline(pipeline_factory):
    p = pipeline_factory()
    run_a(p)
    with pytest.raises(ValueError):
        p.run_followup("fit", "FitFinder", meeting("meeting_c", MEETING_C))


def test_followup_change_report_against_approved_baseline(pipeline_factory):
    responder = Responder()
    p = pipeline_factory(responder)
    v1 = run_a(p)
    p.store.approve(v1, "senior")
    run_a(p)  # an unapproved later draft must NOT become the baseline
    responder.followup = True
    v3 = p.run_followup("fit", "FitFinder", meeting("meeting_c", MEETING_C))
    v = p.store.version(v3)
    report = v["payload"]["change_report"]
    assert v["based_on"] == v1
    assert [r["id"] for r in report["removed"]] == ["REQ-002"]
    assert [m["id"] for m in report["modified"]] == ["REQ-001"]
    assert [a["id"] for a in report["added"]] == ["REQ-004"]
    assert [o["id"] for o in report["out_of_scope"]] == ["REQ-004"]
    assert report["unchanged"] == ["REQ-003"]
    assert report["deadline_change"] == "January thirty first"
    assert v["payload"]["brief"]["deadline"] == "January thirty first"
    ids = [r["id"] for r in v["payload"]["brief"]["requirements"]]
    assert ids == ["REQ-001", "REQ-003", "REQ-004"]  # stable ids, removed one gone
    assert "hours_delta" in report and v["status"] == "draft"
