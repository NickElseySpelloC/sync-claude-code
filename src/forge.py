"""Optional ``pull --forge-orphans``: invent index records for transcripts that have none.

Transcripts without a record on either machine are usually terminal CLI
sessions, so by default they stay out of the sidebar, as on the source. This
reproduces the original repair tool's behaviour with fixes:

- transcripts listed in any record's ``priorCliSessionIds`` are not orphans;
- transcripts with no user or assistant messages are skipped;
- ``cwd`` is read from the transcript rather than guessed from the folder name;
- the new ``sessionId`` is derived from the transcript ID, so re-running never
  creates a second record for the same transcript.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from index import Record

FORGE_NAMESPACE = uuid.UUID("3f5c1f0e-6a7b-4e43-9a51-6b1d2c8e9f10")
DEFAULT_MODEL = "claude-opus-5-5"
TITLE_MAX = 80


@dataclass
class TranscriptMeta:
    """Facts read from a transcript.

    Attributes:
        cwd: Working directory recorded in the transcript, if any.
        title: Best available title.
        created_at: First timestamp, ms since epoch.
        last_activity_at: Last timestamp, ms since epoch.
        completed_turns: Number of assistant messages.
        messages: Number of user plus assistant messages.
        model: Last model used, if recorded.
    """

    cwd: str | None
    title: str
    created_at: int
    last_activity_at: int
    completed_turns: int
    messages: int
    model: str | None


def epoch_ms(value: Any) -> int | None:
    """Convert a transcript timestamp to ms since the epoch.

    Args:
        value: An ISO-8601 string, or a number of seconds or milliseconds.

    Returns:
        Milliseconds since the epoch, or None if the value can't be parsed.
    """
    if isinstance(value, (int, float)):
        return int(value if value > 10**12 else value * 1000)
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value).timestamp() * 1000)
        except ValueError:
            return None
    return None


def _first_text(message: Any) -> str | None:
    """Extract the text of a user message.

    Args:
        message: The ``message`` field of a transcript line.

    Returns:
        The first text block, or None.
    """
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                return str(block["text"])
    return None


def read_transcript(path: Path) -> TranscriptMeta:
    """Scan a ``.jsonl`` transcript line by line.

    Args:
        path: The transcript.

    Returns:
        What could be learned from it.
    """
    cwd: str | None = None
    first_ts: int | None = None
    last_ts: int | None = None
    custom_title: str | None = None
    summary: str | None = None
    first_user: str | None = None
    turns = 0
    messages = 0
    model: str | None = None

    with path.open(encoding="utf-8", errors="replace") as f:
        for raw in f:
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            kind = obj.get("type")
            ts = epoch_ms(obj.get("timestamp"))
            if ts is not None:
                first_ts = ts if first_ts is None else first_ts
                last_ts = ts
            if cwd is None and isinstance(obj.get("cwd"), str):
                cwd = obj["cwd"]
            if kind == "custom-title" and isinstance(obj.get("customTitle"), str):
                custom_title = obj["customTitle"]
            elif kind == "summary" and isinstance(obj.get("summary"), str):
                summary = obj["summary"]
            elif kind == "assistant":
                turns += 1
                messages += 1
                msg = obj.get("message")
                if isinstance(msg, dict) and isinstance(msg.get("model"), str):
                    model = msg["model"]
            elif kind == "user":
                messages += 1
                if first_user is None:
                    first_user = _first_text(obj.get("message"))

    title = custom_title or summary
    if not title and first_user and first_user.strip():
        title = first_user.strip().splitlines()[0][:TITLE_MAX]
    now = int(datetime.now(UTC).timestamp() * 1000)
    return TranscriptMeta(
        cwd=cwd,
        title=title or path.stem,
        created_at=first_ts or now,
        last_activity_at=last_ts or first_ts or now,
        completed_turns=turns,
        messages=messages,
        model=model,
    )


def forged_session_id(cli_session_id: str) -> str:
    """Derive a stable ``sessionId`` for a forged record.

    Args:
        cli_session_id: The transcript ID.

    Returns:
        A ``local_<uuid>`` ID that is the same on every run and machine.
    """
    return f"local_{uuid.uuid5(FORGE_NAMESPACE, cli_session_id)}"


def build_record(cli_session_id: str, meta: TranscriptMeta) -> Record:
    """Build a minimal index record for an orphaned transcript.

    Args:
        cli_session_id: The transcript ID.
        meta: Facts read from the transcript; ``meta.cwd`` must be set.

    Returns:
        The new record.
    """
    return {
        "sessionId": forged_session_id(cli_session_id),
        "cliSessionId": cli_session_id,
        "cwd": meta.cwd,
        "originCwd": meta.cwd,
        "lastFocusedAt": meta.last_activity_at,
        "createdAt": meta.created_at,
        "lastActivityAt": meta.last_activity_at,
        "model": meta.model or DEFAULT_MODEL,
        "effort": "high",
        "isArchived": False,
        "title": meta.title,
        "titleSource": "auto",
        "permissionMode": "acceptEdits",
        "remoteMcpServersConfig": [],
        "completedTurns": meta.completed_turns,
        "alwaysAllowedReasons": [],
        "sessionPermissionUpdates": [],
        "spawnSeed": {},
    }


@dataclass
class ForgeResult:
    """Records forged and transcripts passed over.

    Attributes:
        records: New records keyed by ``sessionId``.
        skipped: Transcript paths skipped, mapped to the reason.
    """

    records: dict[str, Record]
    skipped: dict[str, str]


def forge_orphans(projects_dir: Path, covered: set[str], staging_dir: Path | None = None) -> ForgeResult:
    """Create records for transcripts no record points at.

    Args:
        projects_dir: The ``~/.claude/projects`` folder.
        covered: Every transcript ID referenced by a record, current or prior.
        staging_dir: The staging folder, skipped if it's under ``projects_dir``.

    Returns:
        The forged records and the transcripts skipped.
    """
    result = ForgeResult(records={}, skipped={})
    if not projects_dir.is_dir():
        return result
    for project in sorted(projects_dir.iterdir()):
        if not project.is_dir() or project == staging_dir:
            continue
        for path in sorted(project.glob("*.jsonl")):
            cli_id = path.stem
            if cli_id in covered or cli_id.startswith("agent-"):
                continue
            meta = read_transcript(path)
            if meta.messages == 0:
                result.skipped[str(path)] = "no user or assistant messages"
                continue
            if not meta.cwd:
                result.skipped[str(path)] = "no working directory recorded"
                continue
            rec = build_record(cli_id, meta)
            result.records[rec["sessionId"]] = rec
    return result
