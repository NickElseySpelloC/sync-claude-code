"""``push``: stage the desktop sidebar index where claude-sync will carry it.

Layout under the staging folder (``~/.claude/projects/_desktop-index`` by default)::

    .sync-claude-code              marker: this folder belongs to the tool
    <accountId>/<orgId>/
        manifest.json              source of truth for what was staged
        sessions/local_*.json      verbatim copies of the source records
        archived-sessions.idx
        sidebar.json               sections, pins and stars (optional)
"""

import json
import shutil
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sidebar
from index import read_index_dir
from paths import ARCHIVE_IDX_NAME, IndexDir

MANIFEST_NAME = "manifest.json"
SIDEBAR_NAME = "sidebar.json"
SESSIONS_SUBDIR = "sessions"
MARKER_NAME = ".sync-claude-code"
MANIFEST_SCHEMA = 1


class ExportError(RuntimeError):
    """Raised when the staging folder can't be safely rebuilt."""


@dataclass
class ExportSummary:
    """What ``push`` staged.

    Attributes:
        sessions: Number of records staged, per index key.
        renamed: Number of staged records with a user-set title, per index key.
        archived: Number of archived sessions, per index key.
        sidebar: Whether sidebar settings were staged, per index key.
        warnings: Problems that didn't stop the export.
    """

    sessions: dict[str, int] = field(default_factory=dict)
    renamed: dict[str, int] = field(default_factory=dict)
    archived: dict[str, int] = field(default_factory=dict)
    sidebar: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def load_epitaxy_prefs(desktop_config: Path) -> dict[str, Any] | None:
    """Read ``preferences.epitaxyPrefs`` from the desktop app's config file.

    Args:
        desktop_config: Path to ``claude_desktop_config.json``.

    Returns:
        The prefs dict, or None if the file or key is missing or unreadable.
    """
    try:
        data = json.loads(desktop_config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    prefs = data.get("preferences", {}).get("epitaxyPrefs") if isinstance(data, dict) else None
    return prefs if isinstance(prefs, dict) else None


def reset_staging_dir(staging_dir: Path, claude_dir: Path) -> None:
    """Delete and recreate the staging folder, after checking it is ours.

    Args:
        staging_dir: The staging folder.
        claude_dir: The Claude data folder it must sit inside.

    Raises:
        ExportError: If the folder is outside ``claude_dir``, is a symlink, or
            already holds files without the tool's marker.
    """
    if claude_dir.resolve() not in staging_dir.resolve().parents:
        msg = f"Staging folder {staging_dir} must be inside {claude_dir}"
        raise ExportError(msg)
    if staging_dir.is_symlink():
        msg = f"Staging folder {staging_dir} is a symlink; refusing to use it"
        raise ExportError(msg)
    if staging_dir.exists():
        if any(staging_dir.iterdir()) and not (staging_dir / MARKER_NAME).exists():
            msg = f"{staging_dir} exists and wasn't created by this tool; refusing to delete it"
            raise ExportError(msg)
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)
    (staging_dir / MARKER_NAME).write_text("Staging folder for sync-claude-code. Rebuilt on every push.\n", encoding="utf-8")


def stage(
    index_dirs: list[IndexDir],
    staging_dir: Path,
    claude_dir: Path,
    desktop_config: Path | None,
    tool_version: str,
    now: datetime | None = None,
) -> ExportSummary:
    """Rebuild the staging folder from this machine's index folders.

    Args:
        index_dirs: The index folders to export.
        staging_dir: The staging folder (rebuilt from scratch).
        claude_dir: The Claude data folder; the staging folder must be inside it.
        desktop_config: ``claude_desktop_config.json``, or None to skip sidebar settings.
        tool_version: This tool's version, recorded in the manifest.
        now: Timestamp to record (defaults to the current time).

    Returns:
        A summary of what was staged.
    """
    summary = ExportSummary()
    reset_staging_dir(staging_dir, claude_dir)
    prefs = load_epitaxy_prefs(desktop_config) if desktop_config is not None else None
    if desktop_config is not None and prefs is None:
        summary.warnings.append(f"Couldn't read sidebar settings from {desktop_config}; sections and pins won't be synced")
    stamp = (now or datetime.now(UTC)).isoformat()

    for idx in index_dirs:
        contents = read_index_dir(idx.path)
        summary.warnings.extend(f"{idx.key}: {w}" for w in contents.warnings)
        out_dir = staging_dir / idx.account_id / idx.org_id
        sess_dir = out_dir / SESSIONS_SUBDIR
        sess_dir.mkdir(parents=True)

        for sid in contents.records:
            src = idx.path / f"{sid}.json"
            (sess_dir / src.name).write_bytes(src.read_bytes())
        (out_dir / ARCHIVE_IDX_NAME).write_text(json.dumps({"v": 1, "archived": contents.archived}), encoding="utf-8")

        has_sidebar = False
        if prefs is not None:
            side = sidebar.extract(prefs, idx.key)
            (out_dir / SIDEBAR_NAME).write_text(json.dumps(side, indent=2), encoding="utf-8")
            has_sidebar = True

        manifest = {
            "schema": MANIFEST_SCHEMA,
            "tool": "sync-claude-code",
            "toolVersion": tool_version,
            "sourceHost": socket.gethostname(),
            "sourceHome": str(Path.home()),
            "exportedAt": stamp,
            "accountId": idx.account_id,
            "orgId": idx.org_id,
            "sessions": sorted(contents.records),
            "hasSidebar": has_sidebar,
        }
        (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        summary.sessions[idx.key] = len(contents.records)
        summary.renamed[idx.key] = sum(1 for r in contents.records.values() if r.get("titleSource") == "user")
        summary.archived[idx.key] = len(contents.archived)
        summary.sidebar[idx.key] = has_sidebar
    return summary
