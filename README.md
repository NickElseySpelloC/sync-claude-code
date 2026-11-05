# sync-claude-code

Makes the Claude desktop app's **Code tab** look the same on two Macs: the same sessions, the same titles (including manual renames), the same archived state, the same sidebar sections, pins and stars, and no duplicates.

It works alongside [claude-sync](https://github.com/tawanorg/claude-sync). claude-sync carries the transcripts in `~/.claude`. This tool carries the desktop app's sidebar index, which claude-sync doesn't sync.

```bash
# on the source Mac
./scripts/launch.sh push

# on the target Mac, with Claude.app quit, run from Terminal
./scripts/launch.sh pull
```

Either Mac can be the source. The last `push` wins.

## Why it's needed

The desktop app doesn't build its sidebar from `~/.claude/projects`. It keeps its own index:

| Data | Where it lives |
|---|---|
| Transcripts | `~/.claude/projects/<slug>/<cliSessionId>.jsonl`, synced by claude-sync |
| One record per sidebar session: title, `titleSource`, `cliSessionId`, `priorCliSessionIds`, cwd, … | `~/Library/Application Support/Claude/claude-code-sessions/<accountId>/<orgId>/local_*.json` |
| Archived sessions | the same folder, `archived-sessions.idx` |
| Sidebar sections, pins, stars | `~/Library/Application Support/Claude/claude_desktop_config.json` → `preferences.epitaxyPrefs` |

Without this tool, synced transcripts don't appear in the sidebar on the other Mac. The old version of this repo (`claude-session-repair`) rebuilt the records from the transcripts. That lost manual renames and created duplicates, because one sidebar session can span several transcripts (`priorCliSessionIds`).

## How it works

- **`push`** copies the index records, the archive list, and the sidebar settings into `~/.claude/projects/_desktop-index/`, writes a `manifest.json`, then runs `claude-sync push`. The folder sits under `projects/` because claude-sync's `sessions` scope only syncs `projects`, `history.jsonl`, `tasks` and `plans`. Any other custom path is silently dropped.
- **`pull`** runs `claude-sync pull` and `claude-sync conflicts --keep remote`, backs up the local index to a zip, then merges:
  - Source sessions are written under their own `sessionId`, so IDs are stable on every machine.
  - Local entries that duplicate a source session are deleted: a different `sessionId` whose `cliSessionId` or `priorCliSessionIds` overlap it. This cleans up copies left by the old repair tool.
  - Sessions that exist only on this Mac are kept, unless you pass `--mirror`.
  - A source session whose transcript hasn't arrived yet is skipped and reported, so the sidebar never shows "Session not found on disk". Run `pull` again once claude-sync has the transcript.
  - If a session has newer activity on this Mac than in the staged copy, the local copy is kept.
  - Sidebar sections, pins and stars come from the source. Pins and stars for kept local-only sessions are preserved. Machine-specific settings in `claude_desktop_config.json` are left alone.

The manifest, not the folder listing, decides what's staged. claude-sync never deletes local files on pull, so files from older pushes may linger, and they're ignored.

## Requirements

- macOS, with the Claude desktop app run at least once on each Mac while signed in to the same account
- Python 3.13+ and [uv](https://github.com/astral-sh/uv)
- [claude-sync](https://github.com/tawanorg/claude-sync) set up on both Macs

## Setup

```bash
ln -s .env.dev.template .env.target
```

No claude-sync configuration change is needed.

## Usage

| Command | What it does |
|---|---|
| `push` | Stage this Mac's index and run `claude-sync push` |
| `push --no-sync` | Stage only |
| `pull --dry-run` | Show what would change. Writes nothing and doesn't run claude-sync. Fine while Claude.app is open |
| `pull` | Run `claude-sync pull`, back up, and merge. **Refuses to run while Claude.app is open** |
| `pull --mirror` | Also delete sessions that exist only on this Mac |
| `pull --no-sync` | Skip `claude-sync pull` (use what's already staged) |
| `pull --no-sidebar` | Leave sidebar sections, pins and stars alone |
| `pull --forge-orphans` | Create entries for transcripts that no session points at (see below) |
| `status` | Show the local index, the staged index, the last pull, claude-sync's version and scope, and whether Claude.app is running |

`launch.sh` passes `--config` from `APP_CONFIG`. To run the app directly:

```bash
uv run src/main.py pull --dry-run --config configs/development.yaml
```

### Running pull

1. Quit Claude.app with **Cmd-Q**. It keeps the index in memory and can overwrite changes when it quits.
2. Run `pull` from Terminal.app, not from a terminal inside Claude.app.
3. Start Claude.app.

### Orphaned transcripts

Transcripts with no index entry on either Mac are usually terminal CLI sessions, so they don't appear in the source's sidebar either and aren't added by default. `--forge-orphans` creates entries for them with the old tool's fixes:

- It skips transcripts that are earlier parts of an existing session.
- It skips transcripts with no messages.
- It reads the working folder from the transcript.
- It uses a stable `sessionId`, so running it again never adds a second entry.

## Configuration

`configs/development.yaml`:

| Key | Default | Meaning |
|---|---|---|
| `Paths.ClaudeDir` | `~/.claude` | Claude Code's data folder |
| `Paths.AppSupportDir` | `~/Library/Application Support/Claude` | The desktop app's folder |
| `Paths.StagingDir` | `~/.claude/projects/_desktop-index` | Staging folder. Must be under `ClaudeDir`, and under `projects/` for claude-sync's `sessions` scope |
| `Paths.BackupDir` | `~/Library/Application Support/sync-claude-code/backups` | Zip backups |
| `ClaudeSync.Binary` | `claude-sync` | claude-sync executable |
| `ClaudeSync.RunSync` | `true` | Run claude-sync as part of push and pull |
| `ClaudeSync.AutoResolveConflicts` | `true` | Run `claude-sync conflicts --keep remote` after pulling |
| `Pull.Mirror` | `false` | Default for `--mirror` |
| `Pull.SyncSidebar` | `true` | Merge sidebar sections, pins and stars |

## Safety and rollback

- `pull` zips every index file and `claude_desktop_config.json` into `BackupDir` before changing anything.
- Writes are atomic (temp file, then rename), and the tool refuses to write through symlinks. Session IDs are validated before they're used as file names.
- `push` only reads the desktop app's files. It deletes and rebuilds only its own staging folder, which it marks with a `.sync-claude-code` file.

To roll back, quit Claude.app and unzip the latest backup:

```bash
cd ~/Library/Application\ Support/sync-claude-code/backups && ls -t | head -3
```

The zip holds `claude-code-sessions/<accountId>/<orgId>/…` and `claude_desktop_config.json`. Extract them over `~/Library/Application Support/Claude/`. Delete any `local_*.json` files the pull added first, or extract into an emptied index folder.

## Not covered

- `scheduled-tasks.json`, `side-session-notes/` and `git-worktrees.json`
- Worktree checkouts. A session whose `worktreePath` doesn't exist on this Mac still appears, but it can't be resumed in that worktree.

## Development

```bash
uv sync --extra all
uv run pytest
uv run mypy --strict src
ruff check .
```
