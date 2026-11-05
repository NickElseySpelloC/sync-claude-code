"""Tests for apply.py."""

import zipfile

import pytest
from helpers import make_paths, read_json, rec, sid, write_desktop_config, write_index

from apply import ApplyError, apply_plan, atomic_write, backup, write_epitaxy_prefs
from merge import MergePlan
from paths import find_index_dirs


def test_backup_contains_every_index_file_and_config(tmp_path):
    paths = make_paths(tmp_path)
    write_index(paths, [rec(1), rec(2)], archived=[sid(1)])
    write_desktop_config(paths, {})
    zip_path = backup(find_index_dirs(paths.sessions_root), paths.desktop_config, paths.backup_dir)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert names == {
        f"claude-code-sessions/acct/org/{sid(1)}.json",
        f"claude-code-sessions/acct/org/{sid(2)}.json",
        "claude-code-sessions/acct/org/archived-sessions.idx",
        "claude_desktop_config.json",
    }


def test_apply_plan_writes_deletes_and_archives(tmp_path):
    paths = make_paths(tmp_path)
    index = write_index(paths, [rec(1), rec(2)])
    plan = MergePlan(writes={sid(3): rec(3)}, duplicates={sid(2): sid(3)}, archived=[sid(3)], archived_changed=True)
    apply_plan(index, plan)
    assert read_json(index / f"{sid(3)}.json") == rec(3)
    assert not (index / f"{sid(2)}.json").exists()
    assert (index / f"{sid(1)}.json").exists()
    assert read_json(index / "archived-sessions.idx") == {"v": 1, "archived": [sid(3)]}


def test_records_are_written_compact(tmp_path):
    paths = make_paths(tmp_path)
    index = write_index(paths, [])
    apply_plan(index, MergePlan(writes={sid(1): rec(1)}))
    assert (index / f"{sid(1)}.json").read_text(encoding="utf-8").startswith('{"sessionId":"')


def test_atomic_write_refuses_symlink(tmp_path):
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(ApplyError, match="symlink"):
        atomic_write(link, b"x")
    assert real.read_text(encoding="utf-8") == "{}"


def test_atomic_write_keeps_permissions(tmp_path):
    target = tmp_path / "f.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    atomic_write(target, b"[]")
    assert target.read_bytes() == b"[]"
    assert target.stat().st_mode & 0o777 == 0o600


def test_apply_plan_refuses_symlinked_record(tmp_path):
    paths = make_paths(tmp_path)
    index = write_index(paths, [])
    outside = tmp_path / "outside.json"
    outside.write_text("keep", encoding="utf-8")
    (index / f"{sid(1)}.json").symlink_to(outside)
    with pytest.raises(ApplyError):
        apply_plan(index, MergePlan(writes={sid(1): rec(1)}))
    assert outside.read_text(encoding="utf-8") == "keep"


def test_write_epitaxy_prefs_keeps_other_settings(tmp_path):
    paths = make_paths(tmp_path)
    write_desktop_config(paths, {"a": 1}, other={"sidebarMode": "x"})
    assert write_epitaxy_prefs(paths.desktop_config, {"a": 2}) is True
    data = read_json(paths.desktop_config)
    assert data["preferences"] == {"sidebarMode": "x", "epitaxyPrefs": {"a": 2}}
    assert data["mcpServers"] == {}
    assert write_epitaxy_prefs(paths.desktop_config, {"a": 2}) is False
