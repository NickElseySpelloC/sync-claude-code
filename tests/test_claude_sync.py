"""Tests for claude_sync.py."""

import subprocess  # noqa: S404

import pytest

from claude_sync import ClaudeSync, ClaudeSyncError


def test_missing_binary_raises(mocker):
    mocker.patch("claude_sync.shutil.which", return_value=None)
    with pytest.raises(ClaudeSyncError, match="not found"):
        ClaudeSync().push()


def test_non_zero_exit_raises(mocker):
    mocker.patch("claude_sync.shutil.which", return_value="/bin/claude-sync")
    mocker.patch("claude_sync.subprocess.run", return_value=subprocess.CompletedProcess([], 3))
    with pytest.raises(ClaudeSyncError, match="exit code 3"):
        ClaudeSync().pull()


def test_resolve_conflicts_passes_keep_remote(mocker):
    mocker.patch("claude_sync.shutil.which", return_value="/bin/claude-sync")
    run = mocker.patch("claude_sync.subprocess.run", return_value=subprocess.CompletedProcess([], 0))
    ClaudeSync().resolve_conflicts_remote()
    assert run.call_args.args[0] == ["/bin/claude-sync", "conflicts", "--keep", "remote"]


def test_version_parses_output(mocker):
    mocker.patch("claude_sync.shutil.which", return_value="/bin/claude-sync")
    mocker.patch("claude_sync.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="claude-sync version 1.17.1\n"))
    assert ClaudeSync().version() == "1.17.1"


def test_version_none_when_missing(mocker):
    mocker.patch("claude_sync.shutil.which", return_value=None)
    assert ClaudeSync().version() is None


def test_scope_reads_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("storage:\n  provider: r2\nscope: sessions\n", encoding="utf-8")
    assert ClaudeSync(config_file=cfg).scope() == "sessions"
    cfg.write_text("storage: {}\n", encoding="utf-8")
    assert ClaudeSync(config_file=cfg).scope() == "full"
    assert ClaudeSync(config_file=tmp_path / "missing.yaml").scope() is None
