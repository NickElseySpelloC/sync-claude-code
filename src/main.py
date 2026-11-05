"""sync-claude-code: mirror the Claude desktop app's Code-tab sidebar between Macs.

Run ``push`` on the source machine and ``pull`` on the target. claude-sync
carries the transcripts and the staged sidebar index between them.
"""

import argparse
import json
import os
import subprocess  # noqa: S404
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sc_foundation import SCCommon, SCConfigManager, SCLogger

from claude_sync import ClaudeSync, ClaudeSyncError
from config_schemas import ConfigSchema
from export import MANIFEST_NAME, ExportError, stage
from paths import AppPaths, expand, find_index_dirs
from pull import PullError, PullOptions, PullReport, run_pull
from version import VERSION

CONFIG_FILE = "configs/development.yaml"
LAST_PULL_NAME = "last-pull.json"
DEFAULTS = {
    "ClaudeDir": "~/.claude",
    "AppSupportDir": "~/Library/Application Support/Claude",
    "StagingDir": "~/.claude/projects/_desktop-index",
    "BackupDir": "~/Library/Application Support/sync-claude-code/backups",
}


def _add_common(parser: argparse.ArgumentParser, suppress: bool) -> None:
    """Add ``--config`` and ``--homedir`` to a parser.

    They're accepted before or after the subcommand, because ``launch.sh``
    appends ``--config`` at the end of the command line.

    Args:
        parser: The parser to extend.
        suppress: Leave the attribute unset when absent (for subparsers), so it
            doesn't overwrite a value given before the subcommand.
    """
    default = argparse.SUPPRESS if suppress else None
    parser.add_argument("--config", metavar="FILE", default=default, help=f"Configuration file (default: {CONFIG_FILE})")
    parser.add_argument("--homedir", metavar="PATH", default=default, help="Project home directory")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(prog="sync-claude-code", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    _add_common(parser, suppress=False)
    sub = parser.add_subparsers(dest="command", required=True)

    p_push = sub.add_parser("push", help="Stage this machine's sidebar index and run claude-sync push")
    p_push.add_argument("--no-sync", action="store_true", help="Stage only; don't run claude-sync push")
    _add_common(p_push, suppress=True)

    p_pull = sub.add_parser("pull", help="Run claude-sync pull and merge the staged index into this machine's")
    p_pull.add_argument("--dry-run", action="store_true", help="Show what would change; write nothing and don't run claude-sync")
    p_pull.add_argument("--mirror", action="store_true", default=None, help="Also delete sessions that exist only on this machine")
    p_pull.add_argument("--no-sync", action="store_true", help="Don't run claude-sync pull first")
    p_pull.add_argument("--no-sidebar", action="store_true", help="Don't merge sidebar sections, pins and stars")
    p_pull.add_argument("--forge-orphans", action="store_true", help="Create sidebar entries for transcripts that have none")
    _add_common(p_pull, suppress=True)

    p_status = sub.add_parser("status", help="Show index, staging and claude-sync status")
    _add_common(p_status, suppress=True)
    return parser


def resolve_config_path(config: str | None, homedir: str | None) -> str:
    """Turn ``--config`` and ``--homedir`` into an absolute config file path.

    Args:
        config: The ``--config`` value, if given.
        homedir: The ``--homedir`` value, if given.

    Returns:
        The config file path.
    """
    if homedir:
        home = Path(homedir)
        if not home.is_dir():
            print(f"ERROR: homedir is not a directory: {homedir}", file=sys.stderr)
            sys.exit(1)
        base = home.resolve()
        os.environ["SC_FOUNDATION_PROJECT_ROOT"] = str(base)
    else:
        base = Path(SCCommon.get_project_root())
    path = Path(config or CONFIG_FILE)
    if not path.is_absolute():
        path = base / path
    if not path.is_file():
        print(f"ERROR: configuration file not found: {path}", file=sys.stderr)
        sys.exit(1)
    return str(path.resolve())


def init_config_and_logging(config_file: str) -> tuple[SCConfigManager, SCLogger]:
    """Load the config and start the logger, exiting on failure.

    Args:
        config_file: The config file path.

    Returns:
        The config manager and logger.
    """
    try:
        config = SCConfigManager(config_file=config_file, validation_schema=ConfigSchema().validation)
    except RuntimeError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        logger = SCLogger(config.get_logger_settings())
    except RuntimeError as e:
        print(f"Logger initialisation error: {e}", file=sys.stderr)
        sys.exit(1)
    return config, logger


def app_paths(config: SCConfigManager) -> AppPaths:
    """Build the resolved paths from the config, falling back to defaults.

    Args:
        config: The config manager.

    Returns:
        The resolved paths.
    """

    def get(key: str) -> Path:
        value = config.get("Paths", key, default=None) or DEFAULTS[key]
        return expand(str(value))

    return AppPaths(
        claude_dir=get("ClaudeDir"),
        app_support_dir=get("AppSupportDir"),
        staging_dir=get("StagingDir"),
        backup_dir=get("BackupDir"),
    )


CLAUDE_EXECUTABLE_SUFFIX = ".app/Contents/MacOS/Claude"


def claude_app_running(ps_output: str | None = None) -> bool:
    """Check whether the Claude desktop app is running.

    ``ps`` is used rather than ``pgrep -x Claude``, which can miss the main app
    process (seen when run from a terminal inside Claude.app itself).

    Args:
        ps_output: Output of ``ps -axo comm=``, for testing; read live if None.

    Returns:
        True if a process's executable is ``<something>.app/Contents/MacOS/Claude``.
    """
    if ps_output is None:
        result = subprocess.run(["/bin/ps", "-axo", "comm="], capture_output=True, text=True, check=False)
        ps_output = result.stdout
    return any(line.strip().endswith(CLAUDE_EXECUTABLE_SUFFIX) for line in ps_output.splitlines())


def cmd_push(args: argparse.Namespace, config: SCConfigManager, logger: SCLogger, paths: AppPaths) -> int:
    """Stage the index and run ``claude-sync push``.

    Args:
        args: Parsed arguments.
        config: The config manager.
        logger: The logger.
        paths: Resolved locations.

    Returns:
        The process exit code.
    """
    log = logger.log_message
    if claude_app_running():
        log("Claude.app is running. Its files are only read, but quitting it first avoids copying a half-written file.", "warning")
    index_dirs = find_index_dirs(paths.sessions_root)
    if not index_dirs:
        log(f"No sidebar index found under {paths.sessions_root}. Has Claude.app been run on this machine?", "error")
        return 1
    try:
        summary = stage(index_dirs, paths.staging_dir, paths.claude_dir, paths.desktop_config, VERSION)
    except ExportError as e:
        log(str(e), "error")
        return 1
    for w in summary.warnings:
        log(w, "warning")
    for key, count in summary.sessions.items():
        log(
            f"Staged {key}: {count} sessions ({summary.renamed[key]} renamed, {summary.archived[key]} archived), "
            f"sidebar settings {'included' if summary.sidebar[key] else 'not included'}",
            "summary",
        )
    log(f"Staging folder: {paths.staging_dir}", "detailed")

    if args.no_sync or not config.get("ClaudeSync", "RunSync", default=True):
        log("Skipping claude-sync push. Run 'claude-sync push' yourself to upload.", "summary")
        return 0
    try:
        ClaudeSync(str(config.get("ClaudeSync", "Binary", default="claude-sync") or "claude-sync")).push()
    except ClaudeSyncError as e:
        log(str(e), "error")
        return 1
    log("Push complete. Now run 'pull' on the other machine with Claude.app quit.", "summary")
    return 0


def _log_report(report: PullReport, dry_run: bool, log: Any) -> None:
    """Print what a pull did or would do.

    Args:
        report: The pull report.
        dry_run: Whether this was a dry run.
        log: The logger's ``log_message``.
    """
    for w in report.warnings:
        log(w, "warning")
    for r in report.results:
        p = r.plan
        m = r.staged.manifest
        log(f"Index {r.target.key} <- {m.get('sourceHost', '?')} (pushed {m.get('exportedAt', '?')})", "summary")
        log(
            f"  added {len(p.added) - len(r.forged)}, updated {len(p.updated)}, unchanged {len(p.unchanged)}, "
            f"duplicates removed {len(p.duplicates)}, target-only kept {len(p.kept_target_only)}, "
            f"target-only pruned {len(p.pruned)}, newer here kept {len(p.kept_target_newer)}",
            "summary",
        )
        if r.forged:
            log(f"  forged {len(r.forged)} entries for orphaned transcripts", "summary")
        if p.archived_changed:
            log(f"  archived list changes ({len(p.archived)} archived)", "summary")
        if p.skipped_missing:
            log(f"  skipped {len(p.skipped_missing)} sessions whose transcript isn't on this machine yet; run claude-sync pull and retry", "warning")
        if r.missing_cwds:
            log(f"  {len(r.missing_cwds)} sessions point at a working folder that doesn't exist here", "warning")
        for sid in p.added:
            rec = p.writes[sid]
            log(f"    + {str(rec.get('title', ''))[:70]}", "detailed")
        for sid in p.updated:
            rec = p.writes[sid]
            log(f"    ~ {str(rec.get('title', ''))[:70]}", "detailed")
        for dup, src in p.duplicates.items():
            log(f"    - {dup} (duplicate of {src})", "detailed")
        for sid in p.pruned:
            log(f"    - {sid} (only on this machine)", "detailed")
        for sid in p.skipped_missing:
            log(f"    ! {sid}: transcript {r.staged.contents.records[sid].get('cliSessionId')} missing", "detailed")
    if report.sidebar_changed:
        log("Sidebar sections, pins and stars will be updated" if dry_run else "Sidebar sections, pins and stars updated", "summary")


def cmd_pull(args: argparse.Namespace, config: SCConfigManager, logger: SCLogger, paths: AppPaths) -> int:
    """Run ``claude-sync pull`` and merge the staged index.

    Args:
        args: Parsed arguments.
        config: The config manager.
        logger: The logger.
        paths: Resolved locations.

    Returns:
        The process exit code.
    """
    log = logger.log_message
    if claude_app_running():
        if not args.dry_run:
            log("Claude.app is running. Quit it (Cmd-Q) and run pull again from Terminal; it can overwrite the index when it quits.", "error")
            return 1
        log("Claude.app is running; fine for a dry run, but quit it before the real pull.", "warning")

    options = PullOptions(
        dry_run=args.dry_run,
        mirror=bool(args.mirror if args.mirror is not None else config.get("Pull", "Mirror", default=False)),
        sync_sidebar=not args.no_sidebar and bool(config.get("Pull", "SyncSidebar", default=True)),
        forge_orphans=args.forge_orphans,
    )

    run_sync = not args.dry_run and not args.no_sync and bool(config.get("ClaudeSync", "RunSync", default=True))
    if run_sync:
        cs = ClaudeSync(str(config.get("ClaudeSync", "Binary", default="claude-sync") or "claude-sync"))
        try:
            cs.pull()
            if config.get("ClaudeSync", "AutoResolveConflicts", default=True):
                cs.resolve_conflicts_remote()
        except ClaudeSyncError as e:
            log(str(e), "error")
            return 1

    try:
        report = run_pull(paths, options)
    except PullError as e:
        log(str(e), "error")
        if not run_sync:
            log("claude-sync pull wasn't run (dry run or --no-sync). Run 'claude-sync pull' first, then try again.", "summary")
        return 1

    _log_report(report, args.dry_run, log)
    if not report.has_changes:
        log("Already in sync; nothing to change.", "summary")
    elif args.dry_run:
        log("Dry run: nothing written.", "summary")
    else:
        log(f"Backup written to {report.backup_path}", "summary")
        _record_last_pull(paths, report)
        log("Done. Start Claude.app to see the updated sidebar.", "summary")
    return 0


def _record_last_pull(paths: AppPaths, report: PullReport) -> None:
    """Remember when the last pull happened, for ``status``.

    Args:
        paths: Resolved locations.
        report: The pull report.
    """
    state = {
        "pulledAt": datetime.now(UTC).isoformat(),
        "backup": str(report.backup_path) if report.backup_path else None,
    }
    paths.backup_dir.mkdir(parents=True, exist_ok=True)
    (paths.backup_dir / LAST_PULL_NAME).write_text(json.dumps(state, indent=2), encoding="utf-8")


def cmd_status(config: SCConfigManager, logger: SCLogger, paths: AppPaths) -> int:
    """Report the local index, the staged index and claude-sync's setup.

    Args:
        config: The config manager.
        logger: The logger.
        paths: Resolved locations.

    Returns:
        The process exit code.
    """
    log = logger.log_message
    log(f"sync-claude-code {VERSION}", "summary")
    for idx in find_index_dirs(paths.sessions_root):
        count = len(list(idx.path.glob("local_*.json")))
        log(f"Local index {idx.key}: {count} sessions ({idx.path})", "summary")
    for manifest_path in sorted(paths.staging_dir.glob(f"*/*/{MANIFEST_NAME}")):
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            log(f"Staged {m['accountId']}/{m['orgId']}: {len(m['sessions'])} sessions from {m['sourceHost']} at {m['exportedAt']}", "summary")
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
            log(f"Unreadable manifest {manifest_path}: {e}", "warning")
    last = paths.backup_dir / LAST_PULL_NAME
    if last.exists():
        log(f"Last pull: {json.loads(last.read_text(encoding='utf-8')).get('pulledAt')}", "summary")
    cs = ClaudeSync(str(config.get("ClaudeSync", "Binary", default="claude-sync") or "claude-sync"))
    log(f"claude-sync: version {cs.version() or 'not found'}, scope {cs.scope() or 'unknown'}", "summary")
    log(f"Claude.app running: {'yes' if claude_app_running() else 'no'}", "summary")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        The process exit code.
    """
    args = build_parser().parse_args(argv)
    config_file = resolve_config_path(args.config, args.homedir)
    config, logger = init_config_and_logging(config_file)
    logger.log_message(f"sync-claude-code {args.command} starting (config {config_file})", "debug")
    paths = app_paths(config)
    if args.command == "push":
        return cmd_push(args, config, logger, paths)
    if args.command == "pull":
        return cmd_pull(args, config, logger, paths)
    return cmd_status(config, logger, paths)


if __name__ == "__main__":
    sys.exit(main())
