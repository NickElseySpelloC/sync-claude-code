"""``pull``: decide how to merge a staged source index into the target's.

Everything here is pure: it takes parsed records and returns a plan. Writing
the plan to disk is ``apply.py``'s job.

Rules (see design/sync-claude-code-plan.md):

1. Source records win and keep their ``sessionId``.
2. A target record with a different ``sessionId`` whose transcripts overlap a
   written source record (via ``cliSessionId`` or ``priorCliSessionIds``) is a
   duplicate and is deleted.
3. Target records that overlap nothing on the source are kept (or deleted with
   ``mirror``).
4. A source record whose transcript isn't on this machine is skipped.
5. A target record with the same ``sessionId`` but more recent activity is kept,
   so a session continued on this machine isn't rolled back.
6. Archived state follows whichever side's record is kept.
7. Per-machine view state (``lastFocusedAt``) keeps the later of the two values,
   so clicking into a session on this machine doesn't make every pull rewrite it.
"""

from dataclasses import dataclass, field

from index import Record, activity, cli_ids

# Timestamps that record what this machine's UI did, not what happened in the session.
VIEW_STATE_FIELDS = ("lastFocusedAt",)


def keep_local_view_state(source: Record, target: Record) -> Record:
    """Carry the later of each view-state timestamp into the source record.

    Args:
        source: The source record about to replace ``target``.
        target: The target's existing record with the same ``sessionId``.

    Returns:
        The source record, copied if any field changed.
    """
    out = source
    for key in VIEW_STATE_FIELDS:
        tgt_val = target.get(key)
        src_val = source.get(key)
        if isinstance(tgt_val, (int, float)) and (not isinstance(src_val, (int, float)) or tgt_val > src_val):
            if out is source:
                out = dict(source)
            out[key] = tgt_val
    return out


@dataclass
class MergePlan:
    """The outcome of merging one index folder.

    Attributes:
        writes: Records to write, keyed by ``sessionId``.
        added: IDs of source records new to the target.
        updated: IDs of source records replacing a different target version.
        unchanged: IDs of source records already identical on the target.
        duplicates: Target IDs deleted as duplicates, mapped to the source ID they duplicate.
        pruned: Target-only IDs deleted because of ``mirror``.
        kept_target_only: Target-only IDs kept.
        kept_target_newer: IDs where the target's record is newer and was kept.
        skipped_missing: Source IDs skipped because their transcript is missing.
        archived: The final archived list.
        archived_changed: Whether ``archived`` differs from the target's current list.
    """

    writes: dict[str, Record] = field(default_factory=dict)
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    duplicates: dict[str, str] = field(default_factory=dict)
    pruned: list[str] = field(default_factory=list)
    kept_target_only: list[str] = field(default_factory=list)
    kept_target_newer: list[str] = field(default_factory=list)
    skipped_missing: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    archived_changed: bool = False

    @property
    def deletes(self) -> list[str]:
        """All target IDs to delete."""
        return sorted([*self.duplicates, *self.pruned])

    @property
    def has_changes(self) -> bool:
        """Whether applying the plan would change anything on disk."""
        return bool(self.writes or self.deletes or self.archived_changed)

    def final_ids(self, target: dict[str, Record]) -> set[str]:
        """The session IDs present after the plan is applied.

        Args:
            target: The target's records before the merge.

        Returns:
            The set of session IDs.
        """
        return (set(target) | set(self.writes)) - set(self.deletes)


def merge(
    source: dict[str, Record],
    source_archived: list[str],
    target: dict[str, Record],
    target_archived: list[str],
    transcripts: set[str],
    mirror: bool = False,
) -> MergePlan:
    """Work out how to bring the target index in line with the source.

    Args:
        source: Staged source records keyed by ``sessionId``.
        source_archived: The source's archived list.
        target: The target's current records keyed by ``sessionId``.
        target_archived: The target's current archived list.
        transcripts: IDs of transcripts present on the target.
        mirror: Delete target-only records too.

    Returns:
        The merge plan.
    """
    plan = MergePlan()
    target_ids = {sid: cli_ids(rec) for sid, rec in target.items()}
    touched: set[str] = set()  # target IDs overlapping some source record
    from_source: set[str] = set()  # IDs whose final record comes from the source

    for sid in sorted(source):
        rec = source[sid]
        ids = cli_ids(rec)
        overlaps = [t for t, t_ids in target_ids.items() if t == sid or (ids & t_ids)]
        touched.update(overlaps)

        if rec.get("cliSessionId") not in transcripts:
            plan.skipped_missing.append(sid)
            continue
        if sid in target and activity(target[sid]) > activity(rec):
            plan.kept_target_newer.append(sid)
            continue

        for t in overlaps:
            if t != sid and t not in source:
                plan.duplicates[t] = sid
        from_source.add(sid)
        if sid not in target:
            plan.added.append(sid)
            plan.writes[sid] = rec
        else:
            rec = keep_local_view_state(rec, target[sid])
            if target[sid] != rec:
                plan.updated.append(sid)
                plan.writes[sid] = rec
            else:
                plan.unchanged.append(sid)

    for t in sorted(target):
        if t in touched or t in plan.duplicates:
            continue
        if mirror:
            plan.pruned.append(t)
        else:
            plan.kept_target_only.append(t)

    final = plan.final_ids(target)
    src_arch = set(source_archived)
    tgt_arch = set(target_archived)

    def is_archived(sid: str) -> bool:
        return sid in src_arch if sid in from_source else sid in tgt_arch

    ordered = [s for s in target_archived if s in final and is_archived(s)]
    ordered.extend(s for s in source_archived if s in final and is_archived(s) and s not in ordered)
    plan.archived = ordered
    plan.archived_changed = ordered != list(target_archived)
    return plan
