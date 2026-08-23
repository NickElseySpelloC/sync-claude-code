# Claude Session Repair

Repairs the Desktop "Code" tab sidebar bug where sessions synced or copied into `~/.claude/projects/` (e.g. via `claude-sync`, `rsync`, `tar`) don't appear in the sidebar.

Claude.app only shows sessions that have a matching `local_*.json` index entry under:

```
~/Library/Application Support/Claude/claude-code-sessions/<accountId>/<orgId>/
```

This tool reconstructs those index entries from the `.jsonl` transcript files already present in `~/.claude/projects/`.

## Requirements

- macOS
- Python and [uv](https://github.com/astral-sh/uv)

## Usage

### 1. Quit Claude.app completely

Use **Cmd-Q** and verify no background process lingers in Activity Monitor or the menu bar. The app may overwrite or ignore files written while it is running.

### 2. Dry run (default — writes nothing)

```bash
./scripts/launch.sh
```

Review the printed plan. Each orphaned transcript is shown alongside its reconstructed `cwd`. If any `cwd` looks wrong (see [Ambiguous CWD](#ambiguous-cwd) below), add an override before applying.

### 3. Apply

```bash
./scripts/launch.sh --apply
```

A zip backup of the existing index folder is taken automatically before writing unless `--no-backup` is passed.

### 4. Relaunch Claude.app

The repaired sessions will appear in the Code tab sidebar.

## Options

| Flag | Description |
|---|---|
| `--apply` | Write new index files (default is dry-run) |
| `--no-backup` | Skip the zip backup before writing (not recommended) |
| `--overrides <file>` | JSON file mapping slug → absolute cwd for ambiguous paths |

## Ambiguous CWD

Project slugs are the working directory path with `/` replaced by `-`. This is ambiguous when a real path component itself contains a hyphen (e.g. `dividend-extractor`), because the slug `-Users-nick-dev-dividend-extractor` could mean `/Users/nick/dev/dividend/extractor` or `/Users/nick/dev/dividend-extractor`.

The tool resolves ambiguity with the following strategy (in order):

1. **Overrides file** — always wins; see below.
2. **Known roots heuristic** — if a known root directory name (`dev`, `scripts`, `Projects`, `Documents`, `Desktop`, `src`, `code`, etc.) appears in the slug, everything after it is treated as a single hyphenated leaf name.
3. **Disk walk** — finds the deepest existing directory prefix on this machine and folds the remainder into one leaf name.
4. **Naive split** — last resort; flagged as unconfirmed in the output.

Slugs that could not be confirmed are printed as `AMBIGUOUS CWD` warnings in the dry-run output.

## Creating an Overrides File

If any `cwd` guess is wrong, create a JSON file that maps the slug (the project folder name under `~/.claude/projects/`) to the correct absolute path:

```json
{
  "-Users-nick-dev-dividend-extractor": "/Users/nick/dev/dividend-extractor",
  "-Users-nick-scripts-my-cool-project": "/Users/nick/scripts/my-cool-project"
}
```

Then pass it with `--overrides`:

```bash
./scripts/launch.sh --overrides overrides.json
./scripts/launch.sh --overrides overrides.json --apply
```

The slug is printed in the `AMBIGUOUS CWD` warnings section of the dry-run output, so you can copy it directly from there.

## Safety

- **Never modifies or deletes** existing `local_*.json` files — only adds new ones for transcripts that have no matching entry.
- **Backs up** the entire account/org index folder to a `.zip` before writing anything (unless `--no-backup` is given).
- **Dry-run by default** — nothing is written without `--apply`.
