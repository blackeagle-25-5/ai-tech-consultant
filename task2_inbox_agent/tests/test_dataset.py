from collections import Counter

from task2_inbox_agent.dataset import load


def test_dev_set_has_50_balanced_emails_and_holdout_15():
    dev, holdout = load("dev"), load("holdout")
    assert len(dev) == 50 and len(holdout) == 15
    counts = Counter(e.expected.category for e in dev)
    assert counts == {"how_to": 12, "troubleshooting": 10, "project_request": 6, "refund_cancellation": 6,
                      "complaint": 6, "quote_request": 5, "follow_up": 5}
    ids = [e.email.id for e in dev + holdout]
    assert len(ids) == len(set(ids))


def test_follow_ups_share_a_thread_with_their_original():
    dev = {e.email.id: e.email for e in load("dev")}
    assert dev["F01"].thread_key == dev["D01"].thread_key
