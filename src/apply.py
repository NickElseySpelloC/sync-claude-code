"""Back up the target index and write a merge plan to disk."""

import contextlib
import json
import os
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from merge import MergePlan
from paths import ARCHIVE_IDX_NAME, IndexDir, is_valid_session_id


class ApplyError(RuntimeError):
    """Raised when a write would be unsafe."""


def atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    """Write a file via a temp file and rename, refusing to follow symlinks.

    Args:
        path: Destination file.
        data: Bytes to write.
        mode: Permission bits for the new file; defaults to the existing file's,
            or 0o600 for a new file.

    Raises:
        ApplyError: If the destination or its folder is a symlink.
    """
    if path.is_symlink() or path.parent.is_symlink():
        msg = f"Refusing to write through a symlink: {path}"
        raise ApplyError(msg)
    if mode is None:
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        tmp.chmod(mode)
        tmp.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def safe_delete(index_dir: Path, session_id: str) -> None:
    """Delete one record file from an index folder.

    Args:
        index_dir: The index folder.
        session_id: The record's session ID.

    Raises:
        ApplyError: If the ID is invalid or the file is a symlink.
    """
    if not is_valid_session_id(session_id):
        msg = f"Refusing to delete invalid session ID {session_id!r}"
        raise ApplyError(msg)
    path = index_dir / f"{session_id}.json"
    if path.is_symlink():
        msg = f"Refusing to delete a symlink: {path}"
        raise ApplyError(msg)
    path.unlink(missing_ok=True)


def backup(index_dirs: list[IndexDir], desktop_config: Path | None, backup_dir: Path, now: datetime | None = None) -> Path:
    """Zip every target index folder and the desktop config before writing.

    Args:
        index_dirs: The target index folders.
        desktop_config: ``claude_desktop_config.json``, or None to skip it.
        backup_dir: Folder to put the zip in (created if needed).
        now: Timestamp for the file name (defaults to the current time).

    Returns:
        Path to the zip.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    zip_path = backup_dir / f"index-backup-{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx in index_dirs:
            for f in sorted(idx.path.iterdir()):
                if f.is_file() and not f.is_symlink() and (f.suffix == ".json" or f.name == ARCHIVE_IDX_NAME):
                    zf.write(f, arcname=f"claude-code-sessions/{idx.account_id}/{idx.org_id}/{f.name}")
        if desktop_config is not None and desktop_config.is_file():
            zf.write(desktop_config, arcname=desktop_config.name)
    zip_path.chmod(0o600)
    return zip_path


def encode_record(record: dict[str, Any]) -> bytes:
    """Serialise a record the way the desktop app does (compact, one line).

    Args:
        record: The record.

    Returns:
        UTF-8 JSON bytes.
    """
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def apply_plan(index_dir: Path, plan: MergePlan) -> None:
    """Write a merge plan into a target index folder.

    Args:
        index_dir: The target index folder.
        plan: The plan from ``merge.merge``.

    Raises:
        ApplyError: If a session ID is invalid or a write would follow a symlink.
    """
    for sid, rec in plan.writes.items():
        if not is_valid_session_id(sid):
            msg = f"Refusing to write invalid session ID {sid!r}"
            raise ApplyError(msg)
        atomic_write(index_dir / f"{sid}.json", encode_record(rec))
    for sid in plan.deletes:
        safe_delete(index_dir, sid)
    if plan.archived_changed:
        archived = json.dumps({"v": 1, "archived": plan.archived}, separators=(",", ":"))
        atomic_write(index_dir / ARCHIVE_IDX_NAME, archived.encode("utf-8"))


def write_epitaxy_prefs(desktop_config: Path, prefs: dict[str, Any]) -> bool:
    """Replace ``preferences.epitaxyPrefs`` in the desktop config, keeping everything else.

    Args:
        desktop_config: ``claude_desktop_config.json``.
        prefs: The new ``epitaxyPrefs``.

    Returns:
        True if the file changed.

    Raises:
        ApplyError: If the file isn't a JSON object.
    """
    data = json.loads(desktop_config.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"{desktop_config} is not a JSON object"
        raise ApplyError(msg)
    preferences = data.setdefault("preferences", {})
    if preferences.get("epitaxyPrefs") == prefs:
        return False
    preferences["epitaxyPrefs"] = prefs
    atomic_write(desktop_config, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
    return True
