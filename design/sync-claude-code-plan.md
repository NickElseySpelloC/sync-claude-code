# Plan: rework into `sync-claude-code` (issue #1)

Status: **Approved – implemented**
Branch: `issue-1-sync-claude-code`

## Goal

One utility, run once on the source Mac and once on the target Mac, after which the Claude
desktop app's Code tab on the target mirrors the source: same sessions, same titles
(including manual renames), same archived state, and no duplicates.

```bash
# on the source (e.g. MacMiniAU)
./scripts/launch.sh push

# on the target (e.g. MacBookAir), with Claude.app quit
./scripts/launch.sh pull
```

## Background: why the current approach can't work

| Data | Where it lives | Synced by claude-sync today? |
|---|---|---|
| Transcripts (`*.jsonl`, subagents, tool results) | `~/.claude/projects/<slug>/` | Yes |
| Sidebar index, one file per session: title, `titleSource`, `cliSessionId`, `priorCliSessionIds`, cwd, model, … | `~/Library/Application Support/Claude/claude-code-sessions/<accountId>/<orgId>/local_*.json` | **No** |
| Archived sessions (list of `local_*` IDs) | same folder, `archived-sessions.idx` | **No** |
| Sidebar sections, pins, starred groups | `~/Library/Application Support/Claude/claude_desktop_config.json` → `preferences.epitaxyPrefs` | **No** |

So yes, we still need claude-sync to move `~/.claude`. The current tool rebuilds the
sidebar index from the transcripts alone, which causes two problems:

- **Lost renames.** A rename is stored only in `local_*.json` (`titleSource: "user"`).
- **Duplicates.** One sidebar session can cover several transcripts. The index tracks the
  current one in `cliSessionId` and the earlier ones in `priorCliSessionIds`. The tool only
  checks `cliSessionId`, so each earlier transcript becomes a separate entry. Running it
  again after more syncs adds further copies with new random `local_*` IDs. This was
  confirmed on MacMiniAU: 2 of the 6 transcripts the tool would treat as orphans are already
  listed as earlier transcripts of existing sessions.

The fix is to **ship the source's index across and merge it on the target**, instead of
rebuilding it.

## Design

### Transport

claude-sync is configured with `scope: sessions`. Since claude-sync v1.17.0 that scope is
a hard ceiling: custom sync paths outside `projects`, `history.jsonl`, `tasks` and `plans`
are silently dropped. So `claude-sync paths add desktop-index` would not work. Instead we
stage the desktop index **inside `projects/`**, which is already synced:

```
~/.claude/projects/_desktop-index/
  <accountId>/<orgId>/
    manifest.json            # source host, timestamp, home dir, tool version, list of session IDs
    sessions/local_*.json    # verbatim copies of the source index files
    archived-sessions.idx
```

No claude-sync config change is needed. A side benefit: claude-sync rewrites home paths
in `.json` files under `projects/` (`/Users/nick` ↔ `${HOME}`), so home remapping of
`cwd` and similar fields comes for free. Our own remapping stays as a fallback for
`archived-sessions.idx` and for any path claude-sync misses.

Risks to check during real-machine verification: that Claude Code doesn't treat
`_desktop-index` as a project or delete it in its old-transcript cleanup. The folder
holds no `*.jsonl` files at its top level, which should keep it out of both.

The **manifest is the source of truth**, not the folder listing. claude-sync `pull` only
downloads and never deletes local files, so stale files in the staging folder will
linger. They're ignored because the manifest doesn't list them.

### `push` (source)

1. Find the account/org index folder or folders. Same logic as today, but configurable.
2. Rebuild `~/.claude/projects/_desktop-index/` from scratch: copy the `local_*.json` files and
   `archived-sessions.idx`, then write `manifest.json`.
3. Run `claude-sync push`, streaming its output.
4. Print a summary: number of sessions exported and number of renamed titles.

Claude.app can stay open on the source. We only read its files. It's safer to quit it,
though, because a half-written file is possible. The tool will warn rather than block.

### `pull` (target)

1. **Refuse to run while Claude.app is running** (`pgrep -x Claude`). The app keeps the
   index in memory and can overwrite our changes when it quits.
2. Run `claude-sync pull`. Then run `claude-sync conflicts --keep remote`, because the
   source wins by definition. Both are on by default and configurable.
3. Load the manifest and staged files. If there's nothing staged, stop with a clear message
   telling the user to run `push` on the source first.
4. **Back up** the target index folder, including `archived-sessions.idx`, to a timestamped
   zip.
5. **Merge** (see the next section), then write the files.
6. Print a summary: sessions added, updated, de-duplicated and kept as target-only, plus
   warnings, for example about missing transcripts.

`pull --dry-run` runs steps 1 and 3–6 without writing anything and without pulling.

### Merge rules

For each target index folder that matches a source folder (matched by account and org ID):

1. **Source sessions win.** Each source `local_*.json` is written to the target under the
   **same `sessionId`**. This keeps it stable across runs and keeps `archived-sessions.idx`
   consistent. Home-directory paths are remapped if the two homes differ; for now both are
   `/Users/nick`.
2. **De-duplicate.** Any target entry whose `cliSessionId` or `priorCliSessionIds` overlaps
   a source session's, but which has a different `sessionId`, is deleted. These are the
   duplicates left by earlier repair runs. This cleans up the existing mess on the laptop.
3. **Target-only sessions** are entries that don't overlap anything on the source. They're
   **kept** by default. `--mirror` deletes them as well, for an exact mirror. See open
   question 1.
4. **Archived state**: the source's archived list applies to source sessions. The target's
   own list applies to kept target-only sessions.
5. **Transcripts with no index entry anywhere** are not given a made-up entry by default.
   On the source they don't appear in the sidebar either; they're usually terminal CLI
   sessions or subagents. The old repair behaviour stays available as
   `pull --forge-orphans`, with fixes:
   - It skips IDs listed in any `priorCliSessionIds`.
   - It skips transcripts with no user or assistant messages.
6. **Missing transcripts**: a source entry whose transcript isn't present under
   `~/.claude/projects/` is **skipped and reported**, not written. Otherwise the sidebar
   shows a row that opens onto "Session not found on disk". This happens when claude-sync
   didn't bring the transcript over. Running `pull` again after a fuller sync adds it.

Entries are written whole, as JSON. The app's schema has about 70 fields and keeps
changing, so we copy and never edit any field except the remapped paths.

Writes are **atomic** (temp file, then rename), and the tool **refuses to write through a
symlink**. Both lessons come from the review of claude-sync PR #84.

### Out of scope for this PR (phase 2 candidates)

- Sidebar **sections, pins and starred groups** in `claude_desktop_config.json`. That file
  also holds machine-specific settings, so it needs a careful key-level merge.
- `scheduled-tasks.json`, `side-session-notes/` and `git-worktrees.json`.
- Worktree checkouts. A session whose `worktreePath` doesn't exist on the target will still
  appear, but it can't be resumed in that worktree.

## Code structure

The rename follows `sync-claude-code`. The repo name stays the same unless you want it
changed.

```
src/
  main.py            # CLI entry: argparse subcommands push / pull / status; config + logging
  paths.py           # locate ~/.claude, App Support, index folders; home remapping
  claude_sync.py     # thin subprocess wrapper around the claude-sync binary
  export.py          # push: stage index + manifest
  merge.py           # pull: pure merge logic (input: source + target entries → plan of actions)
  apply.py           # backup + execute merge plan on disk
  forge.py           # optional orphan forging (moved from today's main.py, with fixes)
configs/
  development.yaml   # staging dir name, claude-sync path, auto conflict resolution, mirror default, logging
```

- Uses `SCConfigManager` and `SCLogging` from `sc-foundation-services`, with `--config`
  passed in by `launch.sh`.
- Fully type-hinted, passes `mypy --strict` and `ruff check` cleanly, with Google-style
  docstrings throughout.
- `merge.py` is pure, with no I/O, so it can be unit-tested thoroughly.
- `status` reads only. It shows the index folder, the session count and the time of the
  last push or pull, and claude-sync's version and scope.

## Tests (pytest)

- Replace the stale `tests/test_main.py`, which imports a function that doesn't exist.
- `merge.py`:
  - A new session is added.
  - A renamed title overwrites the old one.
  - A duplicate is removed when it overlaps via `cliSessionId` and when it overlaps via
    `priorCliSessionIds`.
  - A target-only session is kept, and is pruned under `--mirror`.
  - Archived lists are merged correctly.
  - Running twice produces no further changes.
- `export.py`: the manifest is correct and the staging folder is rebuilt cleanly. Tests use
  `tmp_path`.
- `apply.py`: the backup contains every file, and a dry run writes nothing.
- `claude_sync.py`: subprocess is mocked to cover a missing binary and a non-zero exit.
- `merge.py` / `apply.py`: an entry with a missing transcript is skipped and reported; a
  symlinked destination is refused.
- CLI: refuses to `pull` while Claude.app is running (`pgrep` mocked).

## Docs

Rewrite `README.md` for the new two-step workflow, the one-time setup, flags, safety
notes, and how to roll back from a backup zip.

## Verification on real machines

This follows the approach suggested in the issue. With the laptop's `/Users/nick` mounted on
the desktop:

1. Snapshot both index folders and their sidebar screenshots before the run.
2. `push` on MacMiniAU.
3. `pull --dry-run`, then `pull`, on MacBookAir with Claude.app quit.
4. I compare the index folders on both machines. Expect the same set of session IDs, with
   matching titles and archived flags. Laptop-only sessions are expected as extras unless
   `--mirror` is used.
5. Relaunch Claude.app on the laptop and compare the sidebar against the desktop's.
   **Unknown:** whether the app also caches sidebar data elsewhere (for example
   `IndexedDB`), which would make the laptop show stale data. This step will tell us.

## Upstream status (checked 2026-11-05)

claude-sync v1.17.2 has no desktop-index support. Three open PRs add some (#77, #84, #86),
but they're on hold (see issue #91). None of them de-duplicates via `priorCliSessionIds`,
and none syncs sidebar sections or pins. So we build our own. If one of them lands later,
`push`/`pull` must not fight it; we'll revisit then.

## Open questions

1. **Target-only sessions.** Should they be kept by default, with `--mirror` to remove
   them, or should exact mirroring be the default? *My recommendation: keep by default,
   since deleting is irreversible apart from the zip backup.*
   
   > Agree
2. **Sync direction.** Is it always one way (desktop → laptop), or will you sometimes go
   laptop → desktop? The design works both ways, because any machine can `push`. Merging
   edits made on both sides isn't attempted, though; the last `push` wins.
   
   > It can go either way, but I'm Ok with individual directional syncing 
3. **Naming.** Should we rename the repo and project to `sync-claude-code`, or keep
   `claude-session-repair` and rename only the CLI subcommands?
   
   > Rename please inc. this folder and the remote repo
4. **Sidebar sections and pins.** Should they be in this PR or left for phase 2?

   > In this PR - let's go for it! 

## Implementation notes (changes from the plan above)

- **Newer target kept.** If the target's record for the same `sessionId` has a later
  `lastActivityAt` than the staged copy, the target's record is kept. This stops a pull from
  rolling back a session that was continued on this machine after the last push.
- **Index folder matching.** An exact `<accountId>/<orgId>` match is used first, then a
  unique match on `orgId` alone, in case the account segment differs between machines.
- **Dry run while Claude.app is open.** `pull --dry-run` warns instead of refusing, since it
  writes nothing. A real `pull` still refuses.
- **App detection.** The tool uses `ps` and matches `*.app/Contents/MacOS/Claude`. `pgrep -x
  Claude` missed the main process when run from a terminal inside Claude.app.
- **`--forge-orphans`** reads `cwd` from the transcript instead of guessing it from the
  folder slug, and derives a stable `sessionId` from the transcript ID (UUIDv5).
- **Sidebar keys carried:** `starred-local-code-sessions`, `starred-session-groups`,
  `dframe-local-slice` (pins), `dframe-group-scopes`, and this org's entry in
  `dframe-code-sections`. Other `epitaxyPrefs` keys are machine-specific and left alone.
- **Backups** go to `~/Library/Application Support/sync-claude-code/backups/` rather than
  into the app's index folder.
- **Repo folder rename.** The local checkout folder is not renamed by the tool's PR. The
  remote repo is renamed, and GitHub redirects the old URL.
