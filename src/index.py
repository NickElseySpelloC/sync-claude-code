"""Read the desktop app's sidebar index files."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from paths import ARCHIVE_IDX_NAME, is_valid_session_id

Record = dict[str, Any]


@dataclass
class IndexContents:
    """The parsed contents of one index folder.

    Attributes:
        records: Session records keyed by ``sessionId``, in file-name order.
        archived: Archived session IDs, in the file's order.
        warnings: Problems found while reading (unreadable or invalid files).
    """

    records: dict[str, Record] = field(default_factory=dict)
    archived: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def read_record(path: Path) -> Record:
    """Read and validate one ``local_*.json`` record.

    Args:
        path: The record file.

    Returns:
        The parsed record.

    Raises:
        ValueError: If the file isn't a JSON object with a valid ``sessionId``
            matching its file name.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        msg = f"cannot read {path.name}: {e}"
        raise ValueError(msg) from e
    if not isinstance(data, dict):
        msg = f"{path.name} is not a JSON object"
        raise ValueError(msg)  # noqa: TRY004
    sid = data.get("sessionId")
    if not is_valid_session_id(sid) or path.name != f"{sid}.json":
        msg = f"{path.name} has a missing or mismatched sessionId"
        raise ValueError(msg)
    return data


def read_archived(path: Path) -> list[str]:
    """Read an ``archived-sessions.idx`` file.

    Args:
        path: The file. A missing file means nothing is archived.

    Returns:
        The archived session IDs (invalid entries are dropped).

    Raises:
        ValueError: If the file exists but can't be parsed.
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        msg = f"cannot read {path.name}: {e}"
        raise ValueError(msg) from e
    items = data.get("archived", []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        return []
    return [s for s in items if is_valid_session_id(s)]


def read_index_dir(index_dir: Path, records_subdir: str | None = None) -> IndexContents:
    """Read every record and the archive list from an index folder.

    Args:
        index_dir: The folder holding ``archived-sessions.idx``.
        records_subdir: Sub-folder holding the ``local_*.json`` files, if not
            ``index_dir`` itself (the staging layout uses ``sessions/``).

    Returns:
        The folder's contents.
    """
    contents = IndexContents()
    rec_dir = index_dir / records_subdir if records_subdir else index_dir
    for f in sorted(rec_dir.glob("local_*.json")):
        try:
            rec = read_record(f)
        except ValueError as e:
            contents.warnings.append(str(e))
            continue
        contents.records[rec["sessionId"]] = rec
    try:
        contents.archived = read_archived(index_dir / ARCHIVE_IDX_NAME)
    except ValueError as e:
        contents.warnings.append(str(e))
    return contents


def cli_ids(record: Record) -> set[str]:
    """All transcript IDs a record covers: its current one and any earlier ones.

    Args:
        record: A session record.

    Returns:
        The set of ``cliSessionId`` and ``priorCliSessionIds`` values.
    """
    ids: set[str] = set()
    current = record.get("cliSessionId")
    if isinstance(current, str) and current:
        ids.add(current)
    prior = record.get("priorCliSessionIds")
    if isinstance(prior, list):
        ids.update(p for p in prior if isinstance(p, str) and p)
    return ids


def activity(record: Record) -> int:
    """The record's ``lastActivityAt`` as an int (0 if missing).

    Args:
        record: A session record.

    Returns:
        Milliseconds since the epoch.
    """
    value = record.get("lastActivityAt")
    return int(value) if isinstance(value, (int, float)) else 0
