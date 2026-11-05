"""Tests for export.py."""

from datetime import UTC, datetime

import pytest
from helpers import make_paths, read_json, rec, sid, write_desktop_config, write_index

from export import MARKER_NAME, ExportError, stage
from paths import find_index_dirs


def test_stage_writes_manifest_records_and_sidebar(tmp_path):
    paths = make_paths(tmp_path)
    index = write_index(paths, [rec(1, titleSource="user"), rec(2)], archived=[sid(2)])
    write_desktop_config(paths, {"dframe-code-sections": {"acct/org": {"sections": [1]}}, "starred-local-code-sessions": []})

    summary = stage(find_index_dirs(paths.sessions_root), paths.staging_dir, paths.claude_dir, paths.desktop_config, "9.9", now=datetime(2026, 1, 2, tzinfo=UTC))

    out = paths.staging_dir / "acct" / "org"
    manifest = read_json(out / "manifest.json")
    assert manifest["sessions"] == [sid(1), sid(2)]
    assert manifest["toolVersion"] == "9.9"
    assert manifest["exportedAt"] == "2026-01-02T00:00:00+00:00"
    assert manifest["hasSidebar"] is True
    assert (out / "sessions" / f"{sid(1)}.json").read_bytes() == (index / f"{sid(1)}.json").read_bytes()
    assert read_json(out / "archived-sessions.idx")["archived"] == [sid(2)]
    assert read_json(out / "sidebar.json") == {"starred-local-code-sessions": [], "sections": {"sections": [1]}}
    assert summary.sessions == {"acct/org": 2}
    assert summary.renamed == {"acct/org": 1}


def test_stage_rebuilds_cleanly(tmp_path):
    paths = make_paths(tmp_path)
    write_index(paths, [rec(1)])
    dirs = find_index_dirs(paths.sessions_root)
    stage(dirs, paths.staging_dir, paths.claude_dir, None, "1")
    stale = paths.staging_dir / "acct" / "org" / "sessions" / f"{sid(7)}.json"
    stale.write_text("{}", encoding="utf-8")

    stage(dirs, paths.staging_dir, paths.claude_dir, None, "1")

    assert not stale.exists()
    assert (paths.staging_dir / MARKER_NAME).exists()


def test_stage_refuses_foreign_folder(tmp_path):
    paths = make_paths(tmp_path)
    paths.staging_dir.mkdir()
    (paths.staging_dir / "precious.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ExportError, match="wasn't created by this tool"):
        stage([], paths.staging_dir, paths.claude_dir, None, "1")
    assert (paths.staging_dir / "precious.txt").exists()


def test_stage_refuses_folder_outside_claude_dir(tmp_path):
    paths = make_paths(tmp_path)
    with pytest.raises(ExportError, match="must be inside"):
        stage([], tmp_path / "elsewhere", paths.claude_dir, None, "1")


def test_stage_skips_invalid_records_with_warning(tmp_path):
    paths = make_paths(tmp_path)
    index = write_index(paths, [rec(1)])
    (index / f"{sid(2)}.json").write_text("not json", encoding="utf-8")
    summary = stage(find_index_dirs(paths.sessions_root), paths.staging_dir, paths.claude_dir, None, "1")
    assert summary.sessions == {"acct/org": 1}
    assert any("cannot read" in w for w in summary.warnings)
