"""Tests for merge.py."""

from helpers import cid, rec, sid

from merge import merge


def all_transcripts(*numbers: int) -> set[str]:
    return {cid(n) for n in numbers}


def test_new_session_is_added():
    plan = merge({sid(1): rec(1)}, [], {}, [], all_transcripts(1))
    assert plan.added == [sid(1)]
    assert plan.writes == {sid(1): rec(1)}
    assert not plan.deletes


def test_renamed_title_overwrites_target():
    source = {sid(1): rec(1, title="My rename", titleSource="user", activity=2000)}
    target = {sid(1): rec(1, title="auto title", activity=1000)}
    plan = merge(source, [], target, [], all_transcripts(1))
    assert plan.updated == [sid(1)]
    assert plan.writes[sid(1)]["title"] == "My rename"


def test_identical_session_is_unchanged():
    plan = merge({sid(1): rec(1)}, [], {sid(1): rec(1)}, [], all_transcripts(1))
    assert plan.unchanged == [sid(1)]
    assert not plan.has_changes


def test_duplicate_via_cli_session_id_is_removed():
    target = {sid(1): rec(1), sid(99): rec(99, cli=1)}
    plan = merge({sid(1): rec(1)}, [], target, [], all_transcripts(1))
    assert plan.duplicates == {sid(99): sid(1)}
    assert plan.deletes == [sid(99)]


def test_duplicate_via_prior_cli_session_ids_is_removed():
    source = {sid(1): rec(1, cli=2, prior=[1])}
    target = {sid(98): rec(98, cli=1), sid(99): rec(99, cli=2)}
    plan = merge(source, [], target, [], all_transcripts(1, 2))
    assert plan.duplicates == {sid(98): sid(1), sid(99): sid(1)}
    assert plan.added == [sid(1)]


def test_target_entry_listing_source_transcript_as_prior_is_duplicate():
    source = {sid(1): rec(1, cli=1)}
    target = {sid(99): rec(99, cli=5, prior=[1])}
    plan = merge(source, [], target, [], all_transcripts(1))
    assert plan.duplicates == {sid(99): sid(1)}


def test_source_records_are_never_deleted_as_duplicates():
    # The source itself holds two records sharing a transcript; both survive.
    source = {sid(1): rec(1, cli=1), sid(2): rec(2, cli=1)}
    plan = merge(source, [], {sid(2): rec(2, cli=1)}, [], all_transcripts(1))
    assert not plan.duplicates
    assert plan.unchanged == [sid(2)]


def test_target_only_session_is_kept():
    plan = merge({sid(1): rec(1)}, [], {sid(50): rec(50)}, [], all_transcripts(1, 50))
    assert plan.kept_target_only == [sid(50)]
    assert not plan.deletes


def test_target_only_session_is_pruned_with_mirror():
    plan = merge({sid(1): rec(1)}, [], {sid(50): rec(50)}, [], all_transcripts(1, 50), mirror=True)
    assert plan.pruned == [sid(50)]
    assert plan.deletes == [sid(50)]


def test_missing_transcript_is_skipped_and_its_overlaps_kept():
    target = {sid(99): rec(99, cli=1)}
    plan = merge({sid(1): rec(1)}, [], target, [], set(), mirror=True)
    assert plan.skipped_missing == [sid(1)]
    assert not plan.writes
    assert not plan.deletes  # not a duplicate (nothing replaces it), not target-only either


def test_newer_target_record_is_kept():
    source = {sid(1): rec(1, title="old", activity=1000)}
    target = {sid(1): rec(1, title="newer here", activity=5000)}
    plan = merge(source, [], target, [], all_transcripts(1))
    assert plan.kept_target_newer == [sid(1)]
    assert not plan.writes


def test_archived_lists_merge():
    source = {sid(1): rec(1), sid(2): rec(2)}
    target = {sid(1): rec(1), sid(50): rec(50), sid(51): rec(51)}
    # Source archives 2; target had 1 archived (source un-archived it) and 50 archived (target-only).
    plan = merge(source, [sid(2)], target, [sid(1), sid(50)], all_transcripts(1, 2, 50, 51))
    assert plan.archived == [sid(50), sid(2)]
    assert plan.archived_changed


def test_archived_entries_of_deleted_duplicates_are_dropped():
    target = {sid(1): rec(1), sid(99): rec(99, cli=1)}
    plan = merge({sid(1): rec(1)}, [], target, [sid(99)], all_transcripts(1))
    assert plan.archived == []
    assert plan.archived_changed


def test_running_twice_changes_nothing():
    source = {sid(1): rec(1, prior=[7]), sid(2): rec(2)}
    target = {sid(99): rec(99, cli=7), sid(50): rec(50)}
    transcripts = all_transcripts(1, 2, 7, 50)
    first = merge(source, [sid(2)], target, [], transcripts)
    after = {k: v for k, v in {**target, **first.writes}.items() if k not in first.deletes}
    second = merge(source, [sid(2)], after, first.archived, transcripts)
    assert not second.has_changes
