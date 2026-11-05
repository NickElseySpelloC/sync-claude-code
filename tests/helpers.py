"""Shared builders for tests."""

import json
import uuid
from pathlib import Path
from typing import Any

from paths import AppPaths


def sid(n: int) -> str:
    """Make a deterministic ``local_<uuid>`` session ID.

    Args:
        n: A small integer.

    Returns:
        The session ID.
    """
    return f"local_{uuid.UUID(int=n)}"


def cid(n: int) -> str:
    """Make a deterministic transcript ID.

    Args:
        n: A small integer.

    Returns:
        The transcript ID.
    """
    return str(uuid.UUID(int=10_000 + n))


def rec(n: int, cli: int | None = None, prior: list[int] | None = None, title: str = "t", activity: int = 1000, **extra: Any) -> dict[str, Any]:
    """Build a minimal session record.

    Args:
        n: Session number.
        cli: Transcript number (defaults to ``n``).
        prior: Earlier transcript numbers.
        title: Title.
        activity: ``lastActivityAt``.
        **extra: Extra fields.

    Returns:
        The record.
    """
    r: dict[str, Any] = {
        "sessionId": sid(n),
        "cliSessionId": cid(n if cli is None else cli),
        "cwd": "/work/proj",
        "title": title,
        "titleSource": "auto",
        "lastActivityAt": activity,
        "isArchived": False,
    }
    if prior:
        r["priorCliSessionIds"] = [cid(p) for p in prior]
    r.update(extra)
    return r


def make_paths(root: Path) -> AppPaths:
    """Lay out an empty machine under ``root``.

    Args:
        root: A temporary folder.

    Returns:
        The paths for that machine.
    """
    claude = root / ".claude"
    (claude / "projects").mkdir(parents=True)
    support = root / "Support"
    (support / "claude-code-sessions").mkdir(parents=True)
    return AppPaths(
        claude_dir=claude,
        app_support_dir=support,
        staging_dir=claude / "projects" / "_desktop-index",
        backup_dir=root / "backups",
    )


def write_index(paths: AppPaths, records: list[dict[str, Any]], archived: list[str] | None = None, acct: str = "acct", org: str = "org") -> Path:
    """Write records and an archive list into a machine's index folder.

    Args:
        paths: The machine's paths.
        records: Records to write.
        archived: Archived session IDs.
        acct: Account ID.
        org: Org ID.

    Returns:
        The index folder.
    """
    d = paths.sessions_root / acct / org
    d.mkdir(parents=True, exist_ok=True)
    for r in records:
        (d / f"{r['sessionId']}.json").write_text(json.dumps(r, separators=(",", ":")), encoding="utf-8")
    (d / "archived-sessions.idx").write_text(json.dumps({"v": 1, "archived": archived or []}), encoding="utf-8")
    return d


def write_transcripts(paths: AppPaths, numbers: list[int], cwd: str = "/work/proj") -> None:
    """Create transcript files with one user and one assistant message.

    Args:
        paths: The machine's paths.
        numbers: Transcript numbers.
        cwd: Working directory recorded in each transcript.
    """
    proj = paths.projects_dir / "-tmp"
    proj.mkdir(exist_ok=True)
    for n in numbers:
        lines = [
            {"type": "user", "cwd": cwd, "timestamp": "2026-01-01T00:00:00Z", "message": {"content": f"hello {n}"}},
            {"type": "assistant", "cwd": cwd, "timestamp": "2026-01-01T00:01:00Z", "message": {"model": "claude-x", "content": []}},
        ]
        (proj / f"{cid(n)}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def write_desktop_config(paths: AppPaths, epitaxy: dict[str, Any], other: dict[str, Any] | None = None) -> None:
    """Write a ``claude_desktop_config.json``.

    Args:
        paths: The machine's paths.
        epitaxy: The ``epitaxyPrefs`` value.
        other: Other top-level preference keys.
    """
    prefs = dict(other or {})
    prefs["epitaxyPrefs"] = epitaxy
    data = {"mcpServers": {}, "preferences": prefs}
    paths.desktop_config.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    """Read a JSON file.

    Args:
        path: The file.

    Returns:
        The parsed value.
    """
    return json.loads(path.read_text(encoding="utf-8"))
