"""The Inbox Agent pipeline and the human review actions."""

from __future__ import annotations

import difflib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from common.kb_client import KBAnswer, KnowledgeClient, KnowledgeUnavailable
from common.llm import HAIKU, LLM, LLMError
from common.prompts import PromptRegistry
from task2_inbox_agent import guards
from task2_inbox_agent.autonomy import AutonomyManager, is_unchanged
from task2_inbox_agent.schemas import Classification, DraftOutput, Email
from task2_inbox_agent.store import InboxStore, now

TASK_DIR = Path(__file__).resolve().parent
DEFAULT_DB = Path(os.getenv("INBOX_DB", TASK_DIR.parent / "data" / "task2" / "inbox.db"))
PROMPTS = PromptRegistry(TASK_DIR / "prompts")


@dataclass
class ProcessResult:
    interaction_id: int
    decision: str
    reason: str
    classification: Classification | None
    draft: str


def similarity(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, a or "", b or "").ratio(), 4)


class InboxAgent:
    def __init__(self, store: InboxStore, kb: KnowledgeClient, llm: LLM, *,
                 classify_model: str = HAIKU, draft_model: str = HAIKU,
                 prompts: PromptRegistry = PROMPTS) -> None:
        self.store = store
        self.kb = kb
        self.llm = llm
        self.classify_model = classify_model
        self.draft_model = draft_model
        self.prompts = prompts
        self.autonomy = AutonomyManager(store)

    # ------------------------------------------------------------------ model steps
    def classify(self, email: Email, *, version: str | None = None):
        prompt = self.prompts.get("classifier", version)
        user = (f"From: {email.from_name}\nSubject: {email.subject}\n\n" + guards.wrap_untrusted(email.body))
        return self.llm.structured(schema=Classification, system=prompt.system, user=user,
                                   model=self.classify_model, purpose="inbox_classify",
                                   prompt_version=prompt.id, max_tokens=1500, effort="low"), prompt.id

    def draft(self, email: Email, cls: Classification, kb: KBAnswer | None, kb_status: str,
              history: list[dict], examples: list[str], *, version: str | None = None):
        prompt = self.prompts.get("drafter", version)
        parts = [f"Customer: {email.from_name} <{email.from_email}>", f"Subject: {email.subject}",
                 guards.wrap_untrusted(email.body),
                 f"Triage: category={cls.category}, risk={cls.risk}, urgency={cls.urgency}, "
                 f"flags={cls.flags or 'none'}. Summary: {cls.summary}"]
        if kb is not None:
            sources = "\n".join(f"[{i}] {s.title} - {s.url}" for i, s in enumerate(kb.sources, start=1))
            parts.append(f"<help_center status=\"{kb.status}\">\n{kb.answer}\nSources:\n{sources or '(none)'}"
                         "\n</help_center>")
        else:
            parts.append(f"<help_center status=\"{kb_status}\">No help-center lookup.</help_center>")
        if history:
            lines = [f"- Customer earlier: {h['body'][:600]}\n  We replied: {(h['our_reply'] or '(no reply yet)')[:600]}"
                     for h in history[-3:]]
            parts.append("<thread_history>\n" + "\n".join(lines) + "\n</thread_history>")
        if examples:
            parts.append("<approved_examples note=\"Replies our team sent for similar emails. Match their style, "
                         "not their facts.\">\n" + "\n---\n".join(examples) + "\n</approved_examples>")
        return self.llm.structured(schema=DraftOutput, system=prompt.system, user="\n\n".join(parts),
                                   model=self.draft_model, purpose="inbox_draft", prompt_version=prompt.id,
                                   max_tokens=3000, effort="low"), prompt.id

    # ------------------------------------------------------------------ pipeline
    def process(self, email: Email, *, drafter_version: str | None = None) -> ProcessResult:
        start = time.perf_counter()
        self.store.add_email(email)
        history = self.store.thread_history(email.thread_key, email.id)
        cost = 0.0
        try:
            cls_res, cls_prompt = self.classify(email)
        except LLMError as exc:
            iid = self.store.add_interaction(email_id=email.id, decision="error", decision_reason=f"classifier: {exc}")
            self.store.set_email_status(email.id, "error")
            return ProcessResult(iid, "error", str(exc), None, "")
        cls = cls_res.value
        cost += cls_res.record.cost_usd
        if guards.detect_injection(email.subject + "\n" + email.body) and "prompt_injection" not in cls.flags:
            cls.flags.append("prompt_injection")
        kind = cls.category

        # A complaint right after an autonomous reply in the same thread is a quality signal.
        if cls.category == "complaint" and history and history[-1].get("reply_mode") == "autonomous":
            prev_kind = self._kind_of_email(history[-1]["id"])
            if prev_kind:
                self.autonomy.revoke(prev_kind, "system", "customer complained after an autonomous reply")

        kb_answer: KBAnswer | None = None
        kb_status = "not_needed"
        if cls.kb_question and "prompt_injection" not in cls.flags:
            try:
                kb_answer = self.kb.query(cls.kb_question)
                kb_status = kb_answer.status
            except KnowledgeUnavailable as exc:
                iid = self.store.add_interaction(
                    email_id=email.id, kind=kind, classification=cls.model_dump(), kb_status="unavailable",
                    decision="kb_unavailable", decision_reason=str(exc),
                    models={"classifier": self.classify_model}, prompt_versions={"classifier": cls_prompt},
                    cost_usd=cost, latency_ms=int((time.perf_counter() - start) * 1000))
                self.store.set_email_status(email.id, "awaiting_review")
                return ProcessResult(iid, "kb_unavailable", "Knowledge service unavailable: no draft written "
                                     "to avoid inventing facts.", cls, "")

        examples = self.store.approved_examples(kind)
        try:
            draft_res, draft_prompt = self.draft(email, cls, kb_answer, kb_status, history, examples,
                                                 version=drafter_version)
        except LLMError as exc:
            iid = self.store.add_interaction(email_id=email.id, kind=kind, classification=cls.model_dump(),
                                             kb_status=kb_status, decision="error",
                                             decision_reason=f"drafter: {exc}", cost_usd=cost)
            self.store.set_email_status(email.id, "error")
            return ProcessResult(iid, "error", str(exc), cls, "")
        draft = draft_res.value
        cost += draft_res.record.cost_usd
        if kb_answer:
            cost += kb_answer.cost_usd

        allowed_urls = [s.url for s in kb_answer.sources] if kb_answer else []
        evidence = kb_answer.answer if kb_answer else ""
        violations = guards.check_reply(draft.reply, allowed_urls=allowed_urls, evidence_text=evidence)
        decision = self.autonomy.decide(kind, risk=cls.risk, urgency=cls.urgency, flags=list(cls.flags),
                                        kb_status=kb_status, violations=violations,
                                        model_requires_human=draft.requires_human)
        decision_name = {"send": "auto_sent", "audit": "held_for_audit", "human": "queued"}[decision.action]
        iid = self.store.add_interaction(
            email_id=email.id, kind=kind, classification=cls.model_dump(), kb_status=kb_status,
            kb_response=kb_answer.model_dump() if kb_answer else None, draft_text=draft.reply,
            draft_meta=draft.model_dump(exclude={"reply"}), violations=violations, decision=decision_name,
            decision_reason=decision.reason,
            models={"classifier": self.classify_model, "drafter": self.draft_model},
            prompt_versions={"classifier": cls_prompt, "drafter": draft_prompt},
            cost_usd=round(cost, 6), latency_ms=int((time.perf_counter() - start) * 1000))
        if violations:
            self.store.audit("guard", "guard_violation", kind, iid, ", ".join(violations))
        if decision.action == "send":
            self._send(iid, email.id, email.from_email, email.subject, draft.reply, "autonomous")
            self.store.update_interaction(iid, final_text=draft.reply, outcome="auto_sent")
            self.store.set_email_status(email.id, "auto_sent")
            self.store.audit("autonomy", "auto_sent", kind, iid, decision.reason)
        else:
            self.store.set_email_status(email.id, "awaiting_review")
        return ProcessResult(iid, decision_name, decision.reason, cls, draft.reply)

    def _kind_of_email(self, email_id: str) -> str | None:
        row = self.store.conn.execute("SELECT kind FROM interactions WHERE email_id = ? ORDER BY id DESC LIMIT 1",
                                      (email_id,)).fetchone()
        return row[0] if row else None

    def _send(self, iid: int, email_id: str, to_addr: str, subject: str, body: str, mode: str) -> bool:
        subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        return self.store.send(idempotency_key=f"{email_id}:{iid}", email_id=email_id, interaction_id=iid,
                               to_addr=to_addr, subject=subject, body=body, mode=mode)

    # ------------------------------------------------------------------ human review actions
    def _pending(self, interaction_id: int):
        row = self.store.interaction(interaction_id)
        if row["outcome"] != "pending":
            raise ValueError(f"Interaction {interaction_id} was already handled ({row['outcome']}).")
        return row

    def send_reviewed(self, interaction_id: int, final_text: str, reviewer: str, reason: str = "") -> dict:
        """Approve (final_text == draft) or edit-and-send. Unchanged-ness is decided by comparing text."""
        row = self._pending(interaction_id)
        if not final_text.strip():
            raise ValueError("Cannot send an empty reply.")
        violations = guards.check_reply(final_text, allowed_urls=self._allowed_urls(row))
        unchanged = is_unchanged(row["draft_text"] or "", final_text) and not json.loads(row["violations"])
        outcome = "sent_unchanged" if unchanged else "edited"
        if not self._send(interaction_id, row["email_id"], row["from_email"], row["subject"], final_text, "human"):
            raise ValueError("This reply was already sent.")
        self.store.update_interaction(interaction_id, final_text=final_text, outcome=outcome, reviewer=reviewer,
                                      correction_reason=reason or None, reviewed_at=now(),
                                      similarity=similarity(row["draft_text"] or "", final_text))
        self.store.set_email_status(row["email_id"], "sent")
        audited = row["decision"] == "held_for_audit"
        state = self.autonomy.record_human_outcome(row["kind"], unchanged, interaction_id, reviewer, audited)
        self.store.audit(reviewer, f"human_{outcome}", row["kind"], interaction_id,
                         guards.mask_pii(reason) + (f" | reviewer text flagged: {violations}" if violations else ""))
        return {"outcome": outcome, "autonomy": state, "reviewer_text_flags": violations}

    def reject(self, interaction_id: int, replacement: str, reviewer: str, reason: str) -> dict:
        row = self._pending(interaction_id)
        if not replacement.strip():
            raise ValueError("A rejected draft needs a replacement reply.")
        if not self._send(interaction_id, row["email_id"], row["from_email"], row["subject"], replacement, "human"):
            raise ValueError("This reply was already sent.")
        self.store.update_interaction(interaction_id, final_text=replacement, outcome="rejected", reviewer=reviewer,
                                      correction_reason=reason, reviewed_at=now(),
                                      similarity=similarity(row["draft_text"] or "", replacement))
        self.store.set_email_status(row["email_id"], "sent")
        state = self.autonomy.record_human_outcome(row["kind"] or "other", False, interaction_id, reviewer,
                                                   row["decision"] == "held_for_audit")
        self.store.audit(reviewer, "human_rejected", row["kind"], interaction_id, guards.mask_pii(reason))
        return {"outcome": "rejected", "autonomy": state}

    def escalate(self, interaction_id: int, reviewer: str, note: str) -> dict:
        row = self._pending(interaction_id)
        cls = json.loads(row["classification"]) if row["classification"] else {}
        question = cls.get("kb_question") or cls.get("summary") or row["subject"]
        esc_id = None
        try:
            esc_id = self.kb.escalate(question, f"inbox reviewer: {note}")
        except KnowledgeUnavailable:
            pass  # still mark locally; the UI shows that the expert task could not be created
        self.store.update_interaction(interaction_id, outcome="escalated", reviewer=reviewer,
                                      correction_reason=note, reviewed_at=now())
        self.store.set_email_status(row["email_id"], "escalated")
        if row["kind"]:
            self.autonomy.record_human_outcome(row["kind"], False, interaction_id, reviewer,
                                               row["decision"] == "held_for_audit")
        self.store.audit(reviewer, "human_escalated", row["kind"], interaction_id,
                         f"KB escalation id {esc_id}" if esc_id else "KB escalation failed")
        return {"outcome": "escalated", "kb_escalation_id": esc_id}

    @staticmethod
    def _allowed_urls(row) -> list[str]:
        if not row["kb_response"]:
            return []
        return [s["url"] for s in json.loads(row["kb_response"]).get("sources", [])]


def build_agent(db_path: Path | None = None, llm: LLM | None = None, kb: KnowledgeClient | None = None,
                **kwargs) -> InboxAgent:
    return InboxAgent(InboxStore(db_path or DEFAULT_DB), kb or KnowledgeClient(requester="inbox-agent"),
                      llm or LLM(), **kwargs)
