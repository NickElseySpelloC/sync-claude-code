"""
Claude.app Code-tab session index repair tool (macOS).

See README.md for context and usage instructions.
"""

import argparse
import json
import sys
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
APP_SUPPORT_ROOT = Path.home() / "Library" / "Application Support" / "Claude" / "claude-code-sessions"


def find_index_dir() -> Path:
    """Locate the <accountId>/<orgId> index directory.

    If there's exactly one account/org combo, use it automatically; otherwise ask.

    Returns:
        The selected Claude session index directory for the account/org combination.
    """
    if not APP_SUPPORT_ROOT.exists():
        sys.exit(f"Could not find {APP_SUPPORT_ROOT} — is Claude.app installed / has it been run at least once?")

    candidates = []
    for account_dir in APP_SUPPORT_ROOT.iterdir():
        if not account_dir.is_dir():
            continue
        for org_dir in account_dir.iterdir():
            if org_dir.is_dir():
                candidates.append(org_dir)

    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        sys.exit(f"No account/org index folders found under {APP_SUPPORT_ROOT}")

    print("Multiple account/org folders found:")
    for i, c in enumerate(candidates):
        print(f"  [{i}] {c}")
    choice = int(input("Which index? Enter number: ").strip())
    return candidates[choice]


KNOWN_ROOTS = ["dev", "scripts", "Projects", "projects", "Documents", "Desktop", "src", "code", "Code"]


def slug_to_cwd(slug: str, overrides: dict) -> tuple[str, bool]:
    """Reconstruct an absolute path from a ~/.claude/projects/ slug.

    Slugs are the cwd with '/' replaced by '-'. This is AMBIGUOUS when a
    real path component itself contains a hyphen (e.g. 'dividend-extractor'),
    because we can't tell '-dividend-extractor' apart from '-dividend/extractor'.

    Rule: everything after the deepest REAL, EXISTING directory on disk is
    treated as a single hyphenated leaf folder name, not further split.
    e.g. slug '-Users-nick-dev-dividend-extractor':
      - '/Users/nick/dev/dividend' does not exist as a dir
      - '/Users/nick/dev' DOES exist -> stop here, treat remainder
        ['dividend', 'extractor'] as one leaf: 'dividend-extractor'
      - result: /Users/nick/dev/dividend-extractor

    Overrides always win. Falls back to a KNOWN_ROOTS heuristic, then a
    naive full split, if nothing on disk matches (e.g. project since moved
    or deleted).

    Returns:
        A tuple of (reconstructed path, success flag).
    """
    if slug in overrides:
        return overrides[slug], True

    parts = slug.lstrip("-").split("-")

    # Case 0: the whole slug splits cleanly into an existing path already
    # (no hyphenated leaf names involved) — most common case.
    naive_path = "/" + "/".join(parts)
    if Path(naive_path).is_dir():
        return naive_path, True

    # Case 1: KNOWN_ROOTS heuristic takes priority over disk checks. This
    # machine may not have the project actually checked out locally (only
    # session history was synced over), so "does the leaf dir exist here"
    # is not a reliable signal — the split point (e.g. 'dev', 'scripts')
    # is a fixed, known convention regardless of what's on disk. Everything
    # after the first known root is folded into a single hyphenated leaf.
    for root in KNOWN_ROOTS:
        if root in parts:
            idx = parts.index(root)
            prefix = "/" + "/".join(parts[: idx + 1])
            leaf = "-".join(parts[idx + 1 :])
            candidate = f"{prefix}/{leaf}" if leaf else prefix
            return candidate, True

    # Case 2: no known root matched — walk from the full length down,
    # find the deepest prefix that IS a real directory on THIS machine,
    # then fold everything past it into one leaf name. Useful when the
    # project happens to be checked out locally under an unlisted root.
    for i in range(len(parts) - 1, 0, -1):
        prefix_path = "/" + "/".join(parts[:i])
        if Path(prefix_path).is_dir():
            leaf = "-".join(parts[i:])
            candidate = f"{prefix_path}/{leaf}"
            return candidate, Path(candidate).is_dir()

    # Case 3: last resort — naive full split, flagged as unconfirmed.
    return naive_path, False


def epoch_ms(value) -> int:
    """Best-effort conversion of a transcript timestamp field to epoch ms.

    Args:
        value: The timestamp value, which may be an int, float, or ISO string.

    Returns:
        The timestamp in milliseconds since the epoch.
    """
    if isinstance(value, (int, float)):
        # Heuristic: seconds vs ms
        return int(value if value > 10**12 else value * 1000)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))  # noqa: FURB162
            return int(dt.timestamp() * 1000)
        except ValueError:
            pass
    return int(datetime.now(UTC).timestamp() * 1000)


def derive_metadata(jsonl_path: Path) -> dict:
    """Pull title / createdAt / lastActivityAt / completedTurns out of a
    transcript .jsonl by reading it line by line (no full-file JSON parse,
    since these can be large and are technically JSON-lines, not JSON).

    Args:
        jsonl_path: Path to the transcript .jsonl file.

    Returns:
        A dict with keys: title, createdAt, lastActivityAt, completedTurns.
    """  # noqa: D205
    first_ts = None
    last_ts = None
    completed_turns = 0
    title = None
    first_user_text = None

    with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:  # noqa: PLR1702
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = obj.get("timestamp")
            if ts is not None:
                ts_ms = epoch_ms(ts)
                if first_ts is None:
                    first_ts = ts_ms
                last_ts = ts_ms

            obj_type = obj.get("type")

            if obj_type == "summary" and obj.get("summary"):
                title = obj["summary"]

            if obj_type == "assistant":
                completed_turns += 1

            if first_user_text is None and obj_type == "user":
                msg = obj.get("message", {})
                content = msg.get("content")
                if isinstance(content, str):
                    first_user_text = content
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            first_user_text = block.get("text")
                            break

    if not title:
        if first_user_text:
            title = first_user_text.strip().splitlines()[0][:80]
        else:
            title = jsonl_path.stem

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    return {
        "title": title,
        "createdAt": first_ts or now_ms,
        "lastActivityAt": last_ts or first_ts or now_ms,
        "completedTurns": completed_turns,
    }


def forge_entry(cli_session_id: str, cwd: str, meta: dict) -> dict:
    new_id = str(uuid.uuid4())
    return {
        "sessionId": f"local_{new_id}",
        "cliSessionId": cli_session_id,
        "cwd": cwd,
        "originCwd": cwd,
        "lastFocusedAt": meta["lastActivityAt"],
        "createdAt": meta["createdAt"],
        "lastActivityAt": meta["lastActivityAt"],
        "model": "claude-opus-4-8",
        "effort": "high",
        "isArchived": False,
        "title": meta["title"],
        "titleSource": "auto",
        "permissionMode": "acceptEdits",
        "remoteMcpServersConfig": [],
        "chromePermissionMode": "skip_all_permission_checks",
        "completedTurns": meta["completedTurns"],
        "alwaysAllowedReasons": [],
        "sessionPermissionUpdates": [],
        "classifierSummaryEnabled": True,
        "spawnSeed": {},
    }


def backup_index_dir(index_dir: Path) -> Path:
    ts = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    backup_path = index_dir.parent / f"{index_dir.name}-backup-{ts}.zip"
    with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in index_dir.glob("*.json"):
            zf.write(f, arcname=f.name)
    return backup_path


def main():  # noqa: PLR0912, PLR0914
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="Actually write files (default is dry-run)")
    ap.add_argument("--no-backup", action="store_true", help="Skip zip backup before writing (not recommended)")
    ap.add_argument("--overrides", type=Path, default=None,
                     help="JSON file mapping slug -> correct absolute cwd, for ambiguous hyphenated paths")
    args = ap.parse_args()

    if not PROJECTS_DIR.exists():
        sys.exit(f"No such directory: {PROJECTS_DIR}")

    overrides = {}
    if args.overrides and args.overrides.exists():
        overrides = json.loads(args.overrides.read_text())

    index_dir = find_index_dir()
    print(f"Using index directory: {index_dir}\n")

    # Build a set of cliSessionIds already indexed
    existing_cli_ids = set()
    for f in index_dir.glob("local_*.json"):
        try:
            data = json.loads(f.read_text())
            cid = data.get("cliSessionId")
            if cid:
                existing_cli_ids.add(cid)
        except (json.JSONDecodeError, OSError):
            print(f"  [warn] could not read existing index file {f.name}, skipping")

    print(f"Existing indexed sessions: {len(existing_cli_ids)}\n")

    to_forge = []
    ambiguous_warnings = []

    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        slug = project_dir.name
        cwd, confident = slug_to_cwd(slug, overrides)
        if not confident:
            ambiguous_warnings.append((slug, cwd))

        for jsonl_path in sorted(project_dir.glob("*.jsonl")):
            cli_session_id = jsonl_path.stem
            if cli_session_id in existing_cli_ids:
                continue
            if cli_session_id.startswith("agent-"):
                continue  # sub-agent transcripts, not top-level sessions
            meta = derive_metadata(jsonl_path)
            entry = forge_entry(cli_session_id, cwd, meta)
            to_forge.append((jsonl_path, entry))

    print(f"Orphaned transcripts found (no index entry): {len(to_forge)}\n")

    if ambiguous_warnings:
        print("AMBIGUOUS CWD warnings — these slugs contain hyphens that could be")
        print("path separators OR part of a real folder/file name. Best-guess used;")
        print("verify and fix via --overrides if wrong:\n")
        for slug, guess in sorted(set(ambiguous_warnings)):
            print(f"  {slug}  ->  {guess}   (guessed, not confirmed on disk)")
        print()

    for jsonl_path, entry in to_forge:
        print(f"  + {entry['title'][:60]:<60}  cwd={entry['cwd']}")
        print(f"      transcript: {jsonl_path}")
        print(f"      -> {entry['sessionId']}.json  (cliSessionId={entry['cliSessionId']})")

    if not to_forge:
        print("Nothing to do — every transcript already has an index entry.")
        return

    if not args.apply:
        print(f"\nDry run only — no files written. Re-run with --apply to write {len(to_forge)} new index file(s).")
        return

    if not args.no_backup:
        backup_path = backup_index_dir(index_dir)
        print(f"\nBacked up existing index to: {backup_path}")

    written = 0
    for jsonl_path, entry in to_forge:  # noqa: B007
        out_path = index_dir / f"{entry['sessionId']}.json"
        out_path.write_text(json.dumps(entry))
        written += 1

    print(f"\nWrote {written} new index file(s) to {index_dir}")
    print("Quit and relaunch Claude.app (if not already quit) to see the sessions in the sidebar.")


if __name__ == "__main__":
    main()
