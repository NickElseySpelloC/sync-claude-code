"""Tests for the CLI in main.py."""

import shutil
from pathlib import Path

import pytest
from helpers import make_paths, rec, write_index, write_transcripts

import main

PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture
def machine(tmp_path, mocker):
    """A simulated machine with the CLI's config pointed at it.

    Returns:
        The machine's paths and the config file path.
    """
    paths = make_paths(tmp_path / "m")
    write_index(paths, [rec(1)])
    write_transcripts(paths, [1])
    mocker.patch("main.app_paths", return_value=paths)
    cfg = tmp_path / "config.yaml"
    shutil.copy(PROJECT_ROOT / "configs" / "development.yaml", cfg)
    text = cfg.read_text(encoding="utf-8").replace("logs/logfile.log", str(tmp_path / "log.txt"))
    cfg.write_text(text, encoding="utf-8")
    return paths, str(cfg)


def test_config_accepted_after_subcommand():
    args = main.build_parser().parse_args(["pull", "--dry-run", "--config", "x.yaml"])
    assert args.config == "x.yaml"
    assert args.dry_run is True


def test_config_accepted_before_subcommand():
    args = main.build_parser().parse_args(["--config", "x.yaml", "status"])
    assert args.config == "x.yaml"


def test_pull_refuses_while_claude_running(machine, mocker):
    _, cfg = machine
    mocker.patch("main.claude_app_running", return_value=True)
    sync = mocker.patch("main.ClaudeSync")
    assert main.main(["pull", "--config", cfg]) == 1
    sync.assert_not_called()


def test_dry_run_allowed_while_claude_running(machine, mocker):
    paths, cfg = machine
    mocker.patch("main.claude_app_running", return_value=True)
    sync = mocker.patch("main.ClaudeSync")
    assert main.main(["push", "--no-sync", "--config", cfg]) == 0
    assert main.main(["pull", "--dry-run", "--config", cfg]) == 0
    sync.assert_not_called()
    assert not paths.backup_dir.exists()


def test_push_then_pull_runs_claude_sync(machine, mocker):
    _, cfg = machine
    mocker.patch("main.claude_app_running", return_value=False)
    sync = mocker.patch("main.ClaudeSync")
    assert main.main(["push", "--config", cfg]) == 0
    sync.return_value.push.assert_called_once()
    assert main.main(["pull", "--config", cfg]) == 0
    sync.return_value.pull.assert_called_once()
    sync.return_value.resolve_conflicts_remote.assert_called_once()


def test_pull_without_staging_fails(machine, mocker):
    _, cfg = machine
    mocker.patch("main.claude_app_running", return_value=False)
    mocker.patch("main.ClaudeSync")
    assert main.main(["pull", "--no-sync", "--config", cfg]) == 1


def test_status_runs(machine, mocker):
    _, cfg = machine
    mocker.patch("main.claude_app_running", return_value=False)
    assert main.main(["status", "--config", cfg]) == 0


def test_missing_config_exits(tmp_path):
    with pytest.raises(SystemExit):
        main.main(["status", "--config", str(tmp_path / "nope.yaml")])


def test_claude_app_running_detects_main_process_only():
    helpers_only = "/Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper\n/usr/bin/claude\n"
    assert main.claude_app_running(helpers_only) is False
    assert main.claude_app_running(helpers_only + "/Applications/Claude.app/Contents/MacOS/Claude\n") is True
