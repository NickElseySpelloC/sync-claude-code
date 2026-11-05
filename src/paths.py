"""Filesystem locations used by sync-claude-code, and helpers to discover them.

The Claude desktop app keeps its Code-tab sidebar index outside ``~/.claude``:

    ~/Library/Application Support/Claude/claude-code-sessions/<accountId>/<orgId>/
        local_<uuid>.json        one record per sidebar session
        archived-sessions.idx    {"v": 1, "archived": ["local_<uuid>", ...]}

Transcripts live under ``~/.claude/projects/<slug>/<cliSessionId>.jsonl``.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SESSION_ID_RE = re.compile(r"^local_[0-9a-fA-F-]{36}$")
ARCHIVE_IDX_NAME = "archived-sessions.idx"


@dataclass(frozen=True)
class AppPaths:
    """Resolved locations of everything the tool reads or writes.

    Attributes:
        claude_dir: The Claude Code data directory (normally ``~/.claude``).
        app_support_dir: The Claude desktop app's support directory.
        staging_dir: Where ``push`` stages the index for claude-sync to carry.
        backup_dir: Where ``pull`` writes its zip backups.
    """

    claude_dir: Path
    app_support_dir: Path
    staging_dir: Path
    backup_dir: Path

    @property
    def projects_dir(self) -> Path:
        """Directory holding transcript folders."""
        return self.claude_dir / "projects"

    @property
    def sessions_root(self) -> Path:
        """Root of the desktop app's per-account/org index folders."""
        return self.app_support_dir / "claude-code-sessions"

    @property
    def desktop_config(self) -> Path:
        """The desktop app's ``claude_desktop_config.json``."""
        return self.app_support_dir / "claude_desktop_config.json"


@dataclass(frozen=True)
class IndexDir:
    """One ``<accountId>/<orgId>`` sidebar index folder.

    Attributes:
        account_id: The account ID path segment.
        org_id: The organisation ID path segment.
        path: Absolute path to the folder.
    """

    account_id: str
    org_id: str
    path: Path

    @property
    def key(self) -> str:
        """The ``<accountId>/<orgId>`` key the app uses in its preferences."""
        return f"{self.account_id}/{self.org_id}"


def expand(path_str: str) -> Path:
    """Expand ``~`` in a configured path and make it absolute.

    Args:
        path_str: The path as written in the config file.

    Returns:
        The absolute path.
    """
    return Path(path_str).expanduser().absolute()


def find_index_dirs(sessions_root: Path) -> list[IndexDir]:
    """List every ``<accountId>/<orgId>`` folder under the sessions root.

    Args:
        sessions_root: The ``claude-code-sessions`` folder.

    Returns:
        The index folders found, sorted by key. Empty if the root is missing.
    """
    if not sessions_root.is_dir():
        return []
    found: list[IndexDir] = []
    for account_dir in sorted(sessions_root.iterdir()):
        if not account_dir.is_dir() or account_dir.is_symlink():
            continue
        for org_dir in sorted(account_dir.iterdir()):
            if org_dir.is_dir() and not org_dir.is_symlink():
                found.append(IndexDir(account_dir.name, org_dir.name, org_dir))
    return found


def match_index_dir(account_id: str, org_id: str, candidates: list[IndexDir]) -> IndexDir | None:
    """Pick the target index folder that corresponds to a source folder.

    An exact account/org match wins. Failing that, a unique match on org ID is
    used, because the account segment may differ between machines.

    Args:
        account_id: The source account ID.
        org_id: The source org ID.
        candidates: The index folders present on this machine.

    Returns:
        The matching folder, or None if there is no unambiguous match.
    """
    for cand in candidates:
        if cand.account_id == account_id and cand.org_id == org_id:
            return cand
    by_org = [c for c in candidates if c.org_id == org_id]
    if len(by_org) == 1:
        return by_org[0]
    return None


def is_valid_session_id(session_id: object) -> bool:
    """Check that a session ID is safe to use as a file name.

    Args:
        session_id: The value read from a record or an archive list.

    Returns:
        True if it looks like ``local_<uuid>``.
    """
    return isinstance(session_id, str) and SESSION_ID_RE.match(session_id) is not None


def transcript_ids(projects_dir: Path, staging_dir: Path | None = None) -> set[str]:
    """Collect the IDs of all top-level transcripts present on this machine.

    Args:
        projects_dir: The ``~/.claude/projects`` folder.
        staging_dir: The staging folder, if it lives under ``projects_dir``; skipped.

    Returns:
        The set of ``<cliSessionId>`` stems of ``projects/*/*.jsonl`` files.
    """
    if not projects_dir.is_dir():
        return set()
    ids: set[str] = set()
    for project in projects_dir.iterdir():
        if not project.is_dir() or (staging_dir is not None and project == staging_dir):
            continue
        ids.update(f.stem for f in project.glob("*.jsonl"))
    return ids


def remap_home(value: Any, src_home: str, dst_home: str) -> Any:
    """Rewrite home-directory prefixes in every string inside a JSON value.

    Args:
        value: A parsed JSON value.
        src_home: The source machine's home directory, e.g. ``/Users/alice``.
        dst_home: This machine's home directory.

    Returns:
        A copy of the value with ``src_home`` replaced by ``dst_home`` wherever a
        string equals it or starts with it followed by ``/``.
    """
    if src_home == dst_home:
        return value
    if isinstance(value, str):
        if value == src_home:
            return dst_home
        if value.startswith(src_home + "/"):
            return dst_home + value[len(src_home) :]
        return value
    if isinstance(value, list):
        return [remap_home(v, src_home, dst_home) for v in value]
    if isinstance(value, dict):
        return {k: remap_home(v, src_home, dst_home) for k, v in value.items()}
    return value
