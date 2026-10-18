"""The 20-consecutive-unchanged rule, its safeguards and its revocation, through the real agent."""

from task2_inbox_agent.autonomy import is_unchanged

from .conftest import email, how_to, responder

REPLY = "Hi Priya,\n\n1. Go to Design Settings > Logo.\n\nSupport Team"


def approve_n(agent, n, start=1):
    for i in range(start, start + n):
        r = agent.process(email(i))
        assert r.decision == "queued"
        agent.send_reviewed(r.interaction_id, r.draft, reviewer="asha")


def test_unchanged_means_text_equal_after_whitespace_normalisation():
    assert is_unchanged("Hi  Priya,\n\nThanks", "Hi Priya, Thanks ")
    assert not is_unchanged("Hi Priya, thanks", "Hi Priya, thank you")
    assert not is_unchanged("", "")


def test_twenty_unchanged_sends_grant_autonomy(make_agent):
    agent = make_agent(responder(how_to(), REPLY))
    approve_n(agent, 19)
    assert agent.autonomy.state("how_to")["status"] == "learning"
    approve_n(agent, 1, start=20)
    assert agent.autonomy.state("how_to") | {} and agent.autonomy.state("how_to")["status"] == "autonomous"
    r = agent.process(email(21))
    assert r.decision == "auto_sent"
    sent = agent.store.conn.execute("SELECT mode FROM outbox WHERE email_id='E21'").fetchone()
    assert sent["mode"] == "autonomous"


def test_an_edit_resets_the_streak(make_agent):
    agent = make_agent(responder(how_to(), REPLY))
    approve_n(agent, 15)
    r = agent.process(email(16))
    out = agent.send_reviewed(r.interaction_id, REPLY + "\nP.S. thanks!", reviewer="asha", reason="warmer")
    assert out["outcome"] == "edited" and agent.autonomy.state("how_to")["streak"] == 0


def test_risky_email_is_not_auto_sent_even_when_autonomous(make_agent):
    agent = make_agent(responder(how_to(), REPLY))
    approve_n(agent, 20)
    agent.llm.client.responder = responder(how_to(risk="high", flags=["security_sensitive"]), REPLY)
    r = agent.process(email(30))
    assert r.decision == "queued" and "risk high" in r.reason


def test_never_autonomous_kinds_stay_with_humans(make_agent):
    refund = how_to(category="refund_cancellation", risk="low", kb_question=None)
    agent = make_agent(responder(refund, "Hi Priya,\nOur billing team will review this.\nSupport Team"))
    approve_n(agent, 20)
    assert agent.autonomy.state("refund_cancellation")["status"] == "eligible_blocked"
    assert agent.process(email(40)).decision == "queued"


def test_every_tenth_autonomous_email_is_audited_and_edit_revokes(make_agent):
    agent = make_agent(responder(how_to(), REPLY))
    approve_n(agent, 20)
    decisions = [agent.process(email(100 + i)).decision for i in range(10)]
    assert decisions[:9] == ["auto_sent"] * 9 and decisions[9] == "held_for_audit"
    audit_iid = agent.store.conn.execute(
        "SELECT id FROM interactions WHERE decision='held_for_audit'").fetchone()[0]
    agent.send_reviewed(audit_iid, REPLY + " Edited.", reviewer="lead", reason="wrong menu name")
    assert agent.autonomy.state("how_to")["status"] == "learning"
    actions = [r[0] for r in agent.store.conn.execute("SELECT action FROM audit_log")]
    assert "autonomy_granted" in actions and "autonomy_revoked" in actions


def test_guard_violation_blocks_auto_send(make_agent):
    agent = make_agent(responder(how_to(), REPLY))
    approve_n(agent, 20)
    agent.llm.client.responder = responder(how_to(), REPLY + "\nIt will be fixed by tomorrow.")
    r = agent.process(email(50))
    assert r.decision == "queued" and "deadline_commitment" in r.reason


def test_global_switch_and_manual_revoke(make_agent):
    agent = make_agent(responder(how_to(), REPLY))
    approve_n(agent, 20)
    agent.autonomy.set_enabled(False, actor="lead")
    assert agent.process(email(60)).decision == "queued"
    agent.autonomy.set_enabled(True, actor="lead")
    agent.autonomy.revoke("how_to", actor="lead", reason="new product release")
    assert agent.process(email(61)).decision == "queued"


def test_complaint_after_autonomous_reply_revokes(make_agent):
    agent = make_agent(responder(how_to(), REPLY))
    approve_n(agent, 20)
    assert agent.process(email(70, subject="Logo help")).decision == "auto_sent"
    agent.llm.client.responder = responder(how_to(category="complaint", risk="medium", kb_question=None), REPLY)
    agent.process(email(71, subject="Re: Logo help", body="Your answer was wrong and useless."))
    assert agent.autonomy.state("how_to")["status"] == "learning"
