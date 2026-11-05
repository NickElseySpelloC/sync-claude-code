"""Tests for forge.py."""

import json

from helpers import cid, make_paths, write_transcripts

from forge import forge_orphans, forged_session_id, read_transcript


def test_read_transcript_prefers_custom_title(tmp_path):
    path = tmp_path / "t.jsonl"
    lines = [
        {"type": "user", "cwd": "/w", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": [{"type": "text", "text": "first line\nsecond"}]}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:05Z", "message": {"model": "m1"}},
        {"type": "custom-title", "customTitle": "Named"},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n", encoding="utf-8")
    meta = read_transcript(path)
    assert meta.title == "Named"
    assert meta.cwd == "/w"
    assert meta.completed_turns == 1
    assert meta.model == "m1"
    assert meta.last_activity_at - meta.created_at == 5000


def test_forge_skips_covered_agent_and_empty_transcripts(tmp_path):
    paths = make_paths(tmp_path)
    write_transcripts(paths, [1, 2])
    proj = paths.projects_dir / "-tmp"
    (proj / "agent-abc.jsonl").write_text('{"type":"user","cwd":"/x","message":{"content":"hi"}}\n', encoding="utf-8")
    (proj / "empty.jsonl").write_text('{"type":"queue-operation"}\n', encoding="utf-8")

    result = forge_orphans(paths.projects_dir, covered={cid(1)})

    assert list(result.records) == [forged_session_id(cid(2))]
    assert result.records[forged_session_id(cid(2))]["cwd"] == "/work/proj"
    assert any("no user or assistant" in r for r in result.skipped.values())


def test_forged_session_id_is_stable():
    assert forged_session_id("abc") == forged_session_id("abc")
    assert forged_session_id("abc") != forged_session_id("abd")
