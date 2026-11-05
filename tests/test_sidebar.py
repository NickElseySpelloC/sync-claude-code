"""Tests for sidebar.py."""

from helpers import sid

import sidebar

SECTIONS = [{"id": "pinned", "kind": "pinned", "order": 0}, {"id": "mine", "kind": "custom", "name": "Work", "order": 1}]


def test_extract_takes_allow_listed_keys_and_matching_sections():
    prefs = {
        "starred-local-code-sessions": [sid(1)],
        "dframe-local-slice": {"pinnedOrder": [sid(2)], "homeProjectsPinnedOrder": []},
        "dframe-code-sections": {"a/o": {"sections": SECTIONS}, "other/org": {"sections": []}},
        "epitaxy-folder-permission-mode-v2.a": {"/x": "auto"},
    }
    out = sidebar.extract(prefs, "a/o")
    assert out == {
        "starred-local-code-sessions": [sid(1)],
        "dframe-local-slice": {"pinnedOrder": [sid(2)], "homeProjectsPinnedOrder": []},
        "sections": {"sections": SECTIONS},
    }


def test_merge_source_wins_and_machine_settings_survive():
    source = {
        "dframe-local-slice": {"pinnedOrder": [sid(1)]},
        "sections": {"sections": SECTIONS},
    }
    target = {
        "dframe-local-slice": {"pinnedOrder": [sid(9)], "homeProjectsPinnedOrder": ["/p"]},
        "dframe-code-sections": {"t/o": {"sections": []}},
        "epitaxy-folder-permission-mode-v2.a": {"/x": "auto"},
    }
    out = sidebar.merge(source, target, "t/o", keep_ids=set())
    assert out["dframe-local-slice"] == {"pinnedOrder": [sid(1)], "homeProjectsPinnedOrder": ["/p"]}
    assert out["dframe-code-sections"]["t/o"] == {"sections": SECTIONS}
    assert out["epitaxy-folder-permission-mode-v2.a"] == {"/x": "auto"}
    assert target["dframe-local-slice"]["pinnedOrder"] == [sid(9)]  # input not mutated


def test_merge_keeps_pins_of_kept_target_only_sessions():
    source = {"starred-local-code-sessions": [sid(1)]}
    target = {"starred-local-code-sessions": [sid(9), sid(8), sid(1)]}
    out = sidebar.merge(source, target, "t/o", keep_ids={sid(9)})
    assert out["starred-local-code-sessions"] == [sid(1), sid(9)]


def test_merge_creates_sections_dict_when_absent():
    out = sidebar.merge({"sections": {"sections": SECTIONS}}, {}, "t/o", set())
    assert out == {"dframe-code-sections": {"t/o": {"sections": SECTIONS}}}
