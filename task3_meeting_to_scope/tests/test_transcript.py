import pytest

from task3_meeting_to_scope.transcript import chunk, load, parse, verify_quote

from .conftest import MEETING_A


def test_parse_keeps_line_numbers_speakers_and_timestamps():
    t = parse("m", "PLACEHOLDER - test\n" + MEETING_A)
    assert t.placeholder
    first = t.turns[0]
    assert (first.line, first.speaker, first.timestamp) == (2, "Consultant", "00:00:01")


def test_lines_without_speaker_continue_previous_turn():
    t = parse("m", "Client: first part\nsecond part\nConsultant: ok")
    assert t.turns[0].text == "first part second part" and len(t.turns) == 2


def test_no_turns_is_an_error():
    with pytest.raises(ValueError):
        parse("m", "just some notes without speakers")


def test_long_transcript_chunks_overlap():
    t = load("meeting_a")
    chunks = chunk(t, max_chars=2500, overlap_turns=2)
    assert len(chunks) >= 3
    for a, b in zip(chunks, chunks[1:]):
        assert a[-2:] == b[:2]  # overlap preserves context across the boundary
    assert {x.line for c in chunks for x in c} == {x.line for x in t.turns}  # nothing lost


def test_quote_verification():
    t = parse("m", MEETING_A)
    assert verify_quote(t, 2, "filters for specialty and price")
    assert verify_quote(t, 3, "filters for specialty and price")  # neighbouring line tolerated
    assert not verify_quote(t, 2, "we need a native mobile app with payments")


def test_all_three_meetings_load():
    assert {load(m).meeting_id for m in ("meeting_a", "meeting_b", "meeting_c")} == {"meeting_a", "meeting_b",
                                                                                     "meeting_c"}
