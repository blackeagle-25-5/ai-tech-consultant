"""The 20-in-a-row rule, with safeguards and revocation.

Definitions (also in DESIGN.md):
- "Same kind"       = same email category from the classifier (8 fixed categories).
- "Without changing" = the text a human sent equals the AI draft after trimming and collapsing whitespace.
  Clicking "approve" is not enough; we compare the actual text.
- Any edit, rejection, escalation, or failed safety check resets the streak to 0.
- At 20 the kind becomes autonomous, except kinds that always need a human (money, complaints, quotes, scope).
- Even an autonomous kind only auto-sends low-risk, non-urgent emails with a grounded KB answer, no safety
  flags and no guard violations. Every 10th auto-send is held for a human audit instead.
- Autonomy is revoked when an audited draft is edited, when a customer complains after an auto-sent reply,
  on a guard failure, or manually. A global switch turns all autonomy off.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from task2_inbox_agent.store import InboxStore, now

THRESHOLD = 20
AUDIT_EVERY = 10
NEVER_AUTONOMOUS = {"refund_cancellation", "complaint", "quote_request", "project_request", "other"}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def is_unchanged(draft: str, final: str) -> bool:
    return normalize(draft) == normalize(final) and bool(normalize(draft))


@dataclass
class AutoDecision:
    action: str  # send | audit | human
    reason: str


class AutonomyManager:
    def __init__(self, store: InboxStore, threshold: int = THRESHOLD, audit_every: int = AUDIT_EVERY) -> None:
        self.store = store
        self.threshold = threshold
        self.audit_every = audit_every

    @property
    def enabled(self) -> bool:
        return self.store.get_setting("autonomy_enabled", "1") == "1"

    def set_enabled(self, enabled: bool, actor: str) -> None:
        self.store.set_setting("autonomy_enabled", "1" if enabled else "0")
        self.store.audit(actor, "autonomy_global_on" if enabled else "autonomy_global_off")

    def state(self, kind: str) -> dict:
        row = self.store.conn.execute("SELECT * FROM autonomy WHERE kind = ?", (kind,)).fetchone()
        if row is None:
            return {"kind": kind, "streak": 0, "status": "learning", "auto_sent": 0}
        return dict(row)

    def all_states(self) -> list[dict]:
        return [dict(r) for r in self.store.conn.execute("SELECT * FROM autonomy ORDER BY kind")]

    def _save(self, kind: str, streak: int, status: str, auto_sent: int) -> None:
        with self.store.conn:
            self.store.conn.execute(
                """INSERT INTO autonomy(kind, streak, status, auto_sent, updated_at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(kind) DO UPDATE SET streak = excluded.streak, status = excluded.status,
                       auto_sent = excluded.auto_sent, updated_at = excluded.updated_at""",
                (kind, streak, status, auto_sent, now()))

    def record_human_outcome(self, kind: str, unchanged: bool, interaction_id: int, actor: str,
                             audited: bool = False) -> dict:
        s = self.state(kind)
        if unchanged:
            streak = s["streak"] + 1
            status = s["status"]
            if streak >= self.threshold and status == "learning":
                status = "eligible_blocked" if kind in NEVER_AUTONOMOUS else "autonomous"
                self.store.audit("system", "autonomy_granted" if status == "autonomous" else "autonomy_eligible_blocked",
                                 kind, interaction_id, f"{streak} consecutive unchanged sends")
        else:
            streak = 0
            status = s["status"]
            if status == "autonomous":
                status = "learning"
                why = "audited draft was edited" if audited else "a draft of this kind was edited or rejected"
                self.store.audit(actor, "autonomy_revoked", kind, interaction_id, why)
            elif status == "eligible_blocked":
                status = "learning"
        self._save(kind, streak, status, s["auto_sent"])
        return self.state(kind)

    def revoke(self, kind: str, actor: str, reason: str, interaction_id: int | None = None) -> None:
        s = self.state(kind)
        self._save(kind, 0, "learning", s["auto_sent"])
        self.store.audit(actor, "autonomy_revoked", kind, interaction_id, reason)

    def decide(self, kind: str, *, risk: str, urgency: str, flags: list[str], kb_status: str,
               violations: list[str], model_requires_human: bool) -> AutoDecision:
        s = self.state(kind)
        if not self.enabled:
            return AutoDecision("human", "autonomy switched off")
        if s["status"] != "autonomous":
            return AutoDecision("human", f"kind '{kind}' not autonomous (streak {s['streak']}/{self.threshold})")
        blockers = []
        if risk != "low":
            blockers.append(f"risk {risk}")
        if urgency == "high":
            blockers.append("urgent")
        if flags:
            blockers.append("flags: " + ", ".join(flags))
        if kb_status not in ("answered", "not_needed"):
            blockers.append(f"knowledge base status {kb_status}")
        if violations:
            blockers.append("guard: " + ", ".join(violations))
        if model_requires_human:
            blockers.append("drafter asked for a human")
        if blockers:
            return AutoDecision("human", "; ".join(blockers))
        count = s["auto_sent"] + 1
        self._save(kind, s["streak"], s["status"], count)
        if count % self.audit_every == 0:
            return AutoDecision("audit", f"random audit (every {self.audit_every}th autonomous email)")
        return AutoDecision("send", f"autonomous kind '{kind}', all safeguards passed")
