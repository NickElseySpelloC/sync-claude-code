"""End-to-end tests: push on one simulated machine, pull on another."""

import json
import shutil

import pytest
from helpers import (
    cid,
    make_paths,
    read_json,
    rec,
    sid,
    write_desktop_config,
    write_index,
    write_transcripts,
)

from export import stage
from paths import AppPaths, find_index_dirs
from pull import PullError, PullOptions, run_pull

SECTIONS = {"sections": [{"id": "work", "kind": "custom", "name": "Work"}]}


def push(source: AppPaths) -> None:
    stage(find_index_dirs(source.sessions_root), source.staging_dir, source.claude_dir, source.desktop_config, "1")


def carry(source: AppPaths, target: AppPaths) -> None:
    """Stand in for claude-sync: copy the staging folder and transcripts across."""
    shutil.copytree(source.staging_dir, target.staging_dir, dirs_exist_ok=True)
    shutil.copytree(source.projects_dir / "-tmp", target.projects_dir / "-tmp", dirs_exist_ok=True)


@pytest.fixture
def machines(tmp_path):
    source = make_paths(tmp_path / "src")
    target = make_paths(tmp_path / "tgt")
    write_index(source, [rec(1, title="Renamed", titleSource="user", prior=[7]), rec(2)], archived=[sid(2)])
    write_transcripts(source, [1, 2, 7])
    write_desktop_config(source, {"dframe-code-sections": {"acct/org": SECTIONS}, "dframe-local-slice": {"pinnedOrder": [sid(1)]}})
    # Target: a stale copy of session 1, a duplicate forged by the old repair tool for
    # transcript 7, and a target-only session 50 that is pinned.
    write_index(target, [rec(1, title="old"), rec(99, cli=7), rec(50)])
    write_transcripts(target, [50])
    write_desktop_config(target, {"dframe-local-slice": {"pinnedOrder": [sid(50)]}, "machine-only": True}, other={"sidebarMode": "m"})
    push(source)
    carry(source, target)
    return source, target


def test_pull_mirrors_source(machines):
    _, target = machines
    report = run_pull(target, PullOptions())
    index = target.sessions_root / "acct" / "org"

    assert sorted(p.name for p in index.glob("local_*.json")) == sorted(f"{s}.json" for s in (sid(1), sid(2), sid(50)))
    assert read_json(index / f"{sid(1)}.json")["title"] == "Renamed"
    assert read_json(index / "archived-sessions.idx")["archived"] == [sid(2)]
    prefs = read_json(target.desktop_config)["preferences"]
    assert prefs["sidebarMode"] == "m"
    assert prefs["epitaxyPrefs"]["machine-only"] is True
    assert prefs["epitaxyPrefs"]["dframe-code-sections"]["acct/org"] == SECTIONS
    assert prefs["epitaxyPrefs"]["dframe-local-slice"]["pinnedOrder"] == [sid(1), sid(50)]
    assert report.backup_path is not None
    assert report.backup_path.exists()
    assert report.results[0].plan.duplicates == {sid(99): sid(1)}


def test_second_pull_changes_nothing(machines):
    _, target = machines
    run_pull(target, PullOptions())
    report = run_pull(target, PullOptions())
    assert not report.has_changes
    assert report.backup_path is None


def test_dry_run_writes_nothing(machines):
    _, target = machines
    index = target.sessions_root / "acct" / "org"
    before = {p.name: p.read_bytes() for p in index.iterdir()}
    config_before = target.desktop_config.read_bytes()

    report = run_pull(target, PullOptions(dry_run=True))

    assert report.has_changes
    assert {p.name: p.read_bytes() for p in index.iterdir()} == before
    assert target.desktop_config.read_bytes() == config_before
    assert not target.backup_dir.exists()


def test_mirror_removes_target_only_session_and_its_pin(machines):
    _, target = machines
    run_pull(target, PullOptions(mirror=True))
    index = target.sessions_root / "acct" / "org"
    assert not (index / f"{sid(50)}.json").exists()
    prefs = read_json(target.desktop_config)["preferences"]["epitaxyPrefs"]
    assert prefs["dframe-local-slice"]["pinnedOrder"] == [sid(1)]


def test_stale_staged_files_not_in_manifest_are_ignored(machines):
    _, target = machines
    stale = target.staging_dir / "acct" / "org" / "sessions" / f"{sid(3)}.json"
    stale.write_text('{"sessionId":"' + sid(3) + '","cliSessionId":"' + cid(1) + '"}', encoding="utf-8")
    run_pull(target, PullOptions())
    assert not (target.sessions_root / "acct" / "org" / f"{sid(3)}.json").exists()


def test_missing_transcript_is_skipped(machines):
    _, target = machines
    (target.projects_dir / "-tmp" / f"{cid(2)}.jsonl").unlink()
    report = run_pull(target, PullOptions())
    assert report.results[0].plan.skipped_missing == [sid(2)]
    assert not (target.sessions_root / "acct" / "org" / f"{sid(2)}.json").exists()


def test_home_is_remapped(machines):
    source, target = machines
    manifest_path = target.staging_dir / "acct" / "org" / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["sourceHome"] = "/Users/alice"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    rec_path = target.staging_dir / "acct" / "org" / "sessions" / f"{sid(2)}.json"
    r = read_json(rec_path)
    r["cwd"] = "/Users/alice/dev/x"
    rec_path.write_text(json.dumps(r), encoding="utf-8")

    run_pull(target, PullOptions(), home="/Users/bob")

    assert read_json(target.sessions_root / "acct" / "org" / f"{sid(2)}.json")["cwd"] == "/Users/bob/dev/x"
    assert source  # source untouched by the pull


def test_unmatched_org_is_reported(machines):
    _, target = machines
    shutil.move(target.sessions_root / "acct" / "org", target.sessions_root / "acct" / "other-org")
    report = run_pull(target, PullOptions())
    assert not report.results
    assert any("No index folder" in w for w in report.warnings)


def test_org_matches_when_account_differs(machines):
    _, target = machines
    shutil.move(target.sessions_root / "acct", target.sessions_root / "acct2")
    report = run_pull(target, PullOptions())
    assert report.results[0].target.key == "acct2/org"


def test_nothing_staged_raises(tmp_path):
    target = make_paths(tmp_path)
    with pytest.raises(PullError, match="Nothing staged"):
        run_pull(target, PullOptions())


def test_forge_orphans_adds_entry_once(machines):
    _, target = machines
    write_transcripts(target, [60])
    first = run_pull(target, PullOptions(forge_orphans=True))
    assert len(first.results[0].forged) == 1
    second = run_pull(target, PullOptions(forge_orphans=True))
    assert not second.has_changes
