"""Load transcripts, keep line numbers and speakers, split long ones into overlapping chunks,
and check that quotes the model cites really appear in the transcript."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import yaml

TRANSCRIPTS_DIR = Path(__file__).resolve().parent / "transcripts"
LINE_RE = re.compile(r"^\s*(?:\[(?P<ts>[\d:]+)\]\s*)?(?P<speaker>[A-Za-z][\w .'-]{0,40}):\s*(?P<text>.+)$")


@dataclass(frozen=True)
class Turn:
    line: int
    speaker: str
    text: str
    timestamp: str | None


@dataclass
class Transcript:
    meeting_id: str
    date: str
    turns: list[Turn]
    placeholder: bool = False
    raw: str = ""

    def render(self, turns: list[Turn] | None = None) -> str:
        return "\n".join(f"L{t.line} [{t.timestamp or '-'}] {t.speaker}: {t.text}" for t in (turns or self.turns))

    def turn(self, line: int) -> Turn | None:
        return next((t for t in self.turns if t.line == line), None)


def parse(meeting_id: str, text: str, date: str = "", placeholder: bool = False) -> Transcript:
    turns: list[Turn] = []
    for n, raw_line in enumerate(text.splitlines(), start=1):
        m = LINE_RE.match(raw_line)
        if m:
            turns.append(Turn(line=n, speaker=m["speaker"].strip(), text=m["text"].strip(), timestamp=m["ts"]))
        elif turns and raw_line.strip() and not raw_line.startswith("PLACEHOLDER"):
            last = turns[-1]  # continuation of the previous speaker's turn
            turns[-1] = Turn(last.line, last.speaker, last.text + " " + raw_line.strip(), last.timestamp)
    if not turns:
        raise ValueError(f"No speaker turns found in transcript {meeting_id}. Expected 'Speaker: text' lines.")
    return Transcript(meeting_id=meeting_id, date=date, turns=turns,
                      placeholder=placeholder or text.lstrip().startswith("PLACEHOLDER"), raw=text)


def chunk(transcript: Transcript, max_chars: int = 6000, overlap_turns: int = 3) -> list[list[Turn]]:
    """Split by whole speaker turns; consecutive chunks share `overlap_turns` turns of context."""
    chunks: list[list[Turn]] = []
    current: list[Turn] = []
    size = 0
    for t in transcript.turns:
        length = len(t.text) + len(t.speaker) + 12
        if current and size + length > max_chars:
            chunks.append(current)
            current = current[-overlap_turns:] if overlap_turns else []
            size = sum(len(x.text) + len(x.speaker) + 12 for x in current)
        current.append(t)
        size += length
    if current:
        chunks.append(current)
    return chunks


def verify_quote(transcript: Transcript, line: int, quote: str, threshold: float = 0.6) -> bool:
    """True if `quote` appears (approximately) in the cited line or its neighbours."""
    q = _norm(quote)
    if not q:
        return False
    for candidate in (line, line - 1, line + 1):
        turn = transcript.turn(candidate)
        if turn is None:
            continue
        text = _norm(turn.text)
        if q in text:
            return True
        window = len(q)
        best = max((SequenceMatcher(None, q, text[i:i + window]).ratio()
                    for i in range(0, max(1, len(text) - window + 1), max(1, window // 4))), default=0.0)
        if best >= threshold:
            return True
    return False


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower().replace("’", "'")).strip()


def load_meta() -> dict:
    return yaml.safe_load((TRANSCRIPTS_DIR / "meta.yaml").read_text(encoding="utf-8"))


def load(meeting_id: str) -> Transcript:
    for project in load_meta()["projects"].values():
        for m in project["meetings"]:
            if m["id"] == meeting_id:
                text = (TRANSCRIPTS_DIR / m["file"]).read_text(encoding="utf-8")
                return parse(meeting_id, text, date=str(m.get("date", "")), placeholder=m.get("placeholder", False))
    raise KeyError(meeting_id)
