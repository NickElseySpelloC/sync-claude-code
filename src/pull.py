"""``pull``: load the staged source index, merge it, and write the result."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sidebar
from apply import apply_plan, backup, write_epitaxy_prefs
from export import MANIFEST_NAME, SESSIONS_SUBDIR, SIDEBAR_NAME, load_epitaxy_prefs
from forge import forge_orphans
from index import (
    IndexContents,
    Record,
    cli_ids,
    read_archived,
    read_index_dir,
    read_record,
)
from merge import MergePlan, merge
from paths import (
    ARCHIVE_IDX_NAME,
    AppPaths,
    IndexDir,
    find_index_dirs,
    match_index_dir,
    remap_home,
    transcript_ids,
)


class PullError(RuntimeError):
    """Raised when there is nothing usable to pull."""


@dataclass
class PullOptions:
    """Switches for ``pull``.

    Attributes:
        dry_run: Report only; write nothing.
        mirror: Delete target-only sessions.
        sync_sidebar: Merge sidebar sections, pins and stars.
        forge_orphans: Invent records for transcripts that have none.
    """

    dry_run: bool = False
    mirror: bool = False
    sync_sidebar: bool = True
    forge_orphans: bool = False


@dataclass
class Staged:
    """One staged source index folder.

    Attributes:
        manifest: The parsed manifest.
        contents: The staged records and archive list (only those the manifest lists).
        sidebar: The staged sidebar settings, if any.
    """

    manifest: dict[str, Any]
    contents: IndexContents
    sidebar: dict[str, Any] | None


@dataclass
class IndexResult:
    """What happened to one target index folder.

    Attributes:
        target: The target folder.
        staged: The source it was merged from.
        plan: The merge plan.
        forged: IDs of forged records.
        missing_cwds: Written sessions whose working directory doesn't exist here.
    """

    target: IndexDir
    staged: Staged
    plan: MergePlan
    forged: list[str] = field(default_factory=list)
    missing_cwds: list[str] = field(default_factory=list)


@dataclass
class PullReport:
    """The outcome of a pull.

    Attributes:
        results: One entry per merged index folder.
        sidebar_changed: Whether sidebar settings change.
        backup_path: The zip backup written, if any.
        warnings: Problems that didn't stop the pull.
    """

    results: list[IndexResult] = field(default_factory=list)
    sidebar_changed: bool = False
    backup_path: Path | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """Whether anything would be or was written."""
        return self.sidebar_changed or any(r.plan.has_changes for r in self.results)


def load_staged(staging_dir: Path) -> tuple[list[Staged], list[str]]:
    """Read every staged index folder, trusting only what manifests list.

    claude-sync never deletes local files on pull, so stale records from older
    pushes may linger in the staging folder; they are ignored.

    Args:
        staging_dir: The staging folder.

    Returns:
        The staged folders and any warnings.
    """
    staged: list[Staged] = []
    warnings: list[str] = []
    for manifest_path in sorted(staging_dir.glob(f"*/*/{MANIFEST_NAME}")):
        folder = manifest_path.parent
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            warnings.append(f"Skipping {folder}: unreadable manifest ({e})")
            continue
        if not isinstance(manifest, dict) or not isinstance(manifest.get("sessions"), list):
            warnings.append(f"Skipping {folder}: invalid manifest")
            continue
        contents = IndexContents()
        for sid in manifest["sessions"]:
            path = folder / SESSIONS_SUBDIR / f"{sid}.json"
            try:
                rec = read_record(path)
            except ValueError as e:
                warnings.append(f"{folder.parent.name}/{folder.name}: {e}")
                continue
            contents.records[rec["sessionId"]] = rec
        try:
            contents.archived = read_archived(folder / ARCHIVE_IDX_NAME)
        except ValueError as e:
            warnings.append(str(e))
        side: dict[str, Any] | None = None
        if manifest.get("hasSidebar") and (folder / SIDEBAR_NAME).exists():
            try:
                loaded = json.loads((folder / SIDEBAR_NAME).read_text(encoding="utf-8"))
                side = loaded if isinstance(loaded, dict) else None
            except (OSError, json.JSONDecodeError) as e:
                warnings.append(f"Ignoring unreadable {SIDEBAR_NAME}: {e}")
        staged.append(Staged(manifest=manifest, contents=contents, sidebar=side))
    return staged, warnings


def _remap(staged: Staged, home: str) -> None:
    """Rewrite the source's home directory to this machine's, in place.

    claude-sync already does this for JSON under ``projects/``; this is a fallback.

    Args:
        staged: The staged folder.
        home: This machine's home directory.
    """
    src_home = staged.manifest.get("sourceHome")
    if not isinstance(src_home, str) or not src_home or src_home == home:
        return
    staged.contents.records = {sid: remap_home(rec, src_home, home) for sid, rec in staged.contents.records.items()}
    if staged.sidebar is not None:
        staged.sidebar = remap_home(staged.sidebar, src_home, home)


def _add_forged(paths: AppPaths, plan: MergePlan, target: dict[str, Record]) -> list[str]:
    """Add forged records for orphaned transcripts to a plan.

    Args:
        paths: Resolved locations.
        plan: The merge plan, extended in place.
        target: The target's records before the merge.

    Returns:
        The IDs of the forged records added.
    """
    final = plan.final_ids(target)
    covered: set[str] = set()
    for sid in final:
        covered |= cli_ids(plan.writes.get(sid) or target[sid])
    added: list[str] = []
    for sid, rec in forge_orphans(paths.projects_dir, covered, paths.staging_dir).records.items():
        if sid not in final:
            plan.writes[sid] = rec
            plan.added.append(sid)
            added.append(sid)
    return added


def plan_pull(paths: AppPaths, options: PullOptions, home: str | None = None) -> tuple[PullReport, dict[str, Any] | None]:
    """Work out everything ``pull`` would change, without writing.

    Args:
        paths: Resolved locations.
        options: Pull switches.
        home: This machine's home directory (defaults to ``Path.home()``).

    Returns:
        The report, and the new ``epitaxyPrefs`` if the sidebar changes.

    Raises:
        PullError: If nothing is staged.
    """
    report = PullReport()
    staged_list, warnings = load_staged(paths.staging_dir)
    report.warnings.extend(warnings)
    if not staged_list:
        msg = f"Nothing staged in {paths.staging_dir}. Run 'push' on the source machine first, then make sure claude-sync has pulled."
        raise PullError(msg)

    targets = find_index_dirs(paths.sessions_root)
    transcripts = transcript_ids(paths.projects_dir, paths.staging_dir)
    prefs = load_epitaxy_prefs(paths.desktop_config) if options.sync_sidebar else None
    new_prefs = prefs
    my_home = home or str(Path.home())

    for staged in staged_list:
        target = match_index_dir(str(staged.manifest.get("accountId")), str(staged.manifest.get("orgId")), targets)
        if target is None:
            report.warnings.append(
                f"No index folder on this machine matches {staged.manifest.get('accountId')}/{staged.manifest.get('orgId')}; "
                "open Claude.app once while signed in to the same account, then retry."
            )
            continue
        _remap(staged, my_home)
        current = read_index_dir(target.path)
        report.warnings.extend(f"{target.key}: {w}" for w in current.warnings)
        plan = merge(
            staged.contents.records,
            staged.contents.archived,
            current.records,
            current.archived,
            transcripts,
            mirror=options.mirror,
        )
        result = IndexResult(target=target, staged=staged, plan=plan)

        if options.forge_orphans:
            result.forged = _add_forged(paths, plan, current.records)

        result.missing_cwds = sorted(
            sid for sid, rec in plan.writes.items() if isinstance(rec.get("cwd"), str) and not Path(rec["cwd"]).exists()
        )
        report.results.append(result)

        if new_prefs is not None and staged.sidebar is not None:
            keep_ids = set(plan.kept_target_only) | set(result.forged)
            new_prefs = sidebar.merge(staged.sidebar, new_prefs, target.key, keep_ids)

    if options.sync_sidebar and prefs is None:
        report.warnings.append(f"Couldn't read {paths.desktop_config}; sidebar sections and pins not merged")
    report.sidebar_changed = new_prefs is not None and new_prefs != prefs
    return report, (new_prefs if report.sidebar_changed else None)


def run_pull(paths: AppPaths, options: PullOptions, home: str | None = None) -> PullReport:
    """Plan the pull and, unless it's a dry run, back up and write it.

    Args:
        paths: Resolved locations.
        options: Pull switches.
        home: This machine's home directory (defaults to ``Path.home()``).

    Returns:
        The report.
    """
    report, new_prefs = plan_pull(paths, options, home)
    if options.dry_run or not report.has_changes:
        return report
    targets = [r.target for r in report.results]
    report.backup_path = backup(targets, paths.desktop_config, paths.backup_dir)
    for result in report.results:
        apply_plan(result.target.path, result.plan)
    if new_prefs is not None:
        write_epitaxy_prefs(paths.desktop_config, new_prefs)
    return report
