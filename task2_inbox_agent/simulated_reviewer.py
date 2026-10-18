"""A deterministic stand-in for a human reviewer, used ONLY for repeatable learning experiments.

It applies a fixed "house style" that is NOT written in the v1 drafter prompt. Real reviewer edits in
the UI flow through exactly the same learning code; this simulation just lets us measure improvement
on 20+ emails without a person editing every draft. It is labelled as simulated in EVALS.md.

House style rules:
  R1 First line is "Hi <first name>,"
  R2 Sign-off is exactly "Best regards,\\nNorthstar Support Team"
  R3 At most 170 words
  R4 When help-center sources exist, include one source link on a line starting with "Guide: "
  R5 No filler phrases (see BANNED)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SIGN_OFF = "Best regards,\nNorthstar Support Team"
MAX_WORDS = 170
BANNED = ["i apologize for any inconvenience", "rest assured", "don't hesitate to", "do not hesitate to",
          "i hope this email finds you well", "i hope this helps", "please feel free to"]
CLOSINGS = re.compile(r"\n\s*(best regards|kind regards|regards|warm regards|best|thanks|thank you|cheers|sincerely)"
                      r"[,!.]?\s*\n.*\Z", re.I | re.S)


@dataclass
class Review:
    final_text: str
    violations: list[str]

    @property
    def unchanged(self) -> bool:
        return not self.violations


def review(draft: str, first_name: str, source_urls: list[str]) -> Review:
    text = draft.strip()
    violations: list[str] = []

    # R5 filler phrases: remove the sentence containing them.
    for phrase in BANNED:
        if phrase in text.lower():
            violations.append("R5_filler")
            text = re.sub(r"[^.!?\n]*" + re.escape(phrase) + r"[^.!?\n]*[.!?]?\s*", "", text, flags=re.I)
            break

    # R1 greeting
    greeting = f"Hi {first_name},"
    lines = text.splitlines()
    if not lines or lines[0].strip() != greeting:
        violations.append("R1_greeting")
        if lines and re.match(r"^(hi|hello|dear|hey|good (morning|afternoon))\b", lines[0].strip(), re.I):
            lines = lines[1:]
        text = greeting + "\n\n" + "\n".join(lines).strip()

    # R2 sign-off
    if not text.endswith(SIGN_OFF):
        violations.append("R2_signoff")
        text = CLOSINGS.sub("", text).rstrip() + "\n\n" + SIGN_OFF

    # R4 guide link
    if source_urls and not re.search(r"^Guide: https?://", text, re.M):
        violations.append("R4_guide_link")
        text = text.replace("\n\n" + SIGN_OFF, f"\n\nGuide: {source_urls[0]}\n\n{SIGN_OFF}")

    # R3 length: trim the longest middle paragraph sentences until short enough.
    if len(text.split()) > MAX_WORDS:
        violations.append("R3_too_long")
        head, _, rest = text.partition("\n\n")
        body, _, tail = rest.rpartition("\n\n" + SIGN_OFF)
        body_words = body.split()
        keep = MAX_WORDS - len(head.split()) - len(SIGN_OFF.split()) - 2
        body = " ".join(body_words[:max(keep, 20)]).rstrip(",;") + "..."
        text = f"{head}\n\n{body}\n\n{SIGN_OFF}"

    return Review(final_text=text, violations=violations)
