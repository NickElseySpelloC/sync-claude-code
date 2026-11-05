"""Sidebar sections, pins and starred items, stored in ``claude_desktop_config.json``.

They live under ``preferences.epitaxyPrefs`` alongside many machine-specific
settings, so only an allow-list of keys is carried between machines.
"""

import copy
from typing import Any

from paths import is_valid_session_id

# Keys copied whole (subject to the merge rules below).
SHARED_KEYS = (
    "starred-local-code-sessions",
    "starred-session-groups",
    "dframe-local-slice",
    "dframe-group-scopes",
)
# Keyed by "<accountId>/<orgId>"; only the matching entry is carried.
SECTIONS_KEY = "dframe-code-sections"


def extract(prefs: dict[str, Any], index_key: str) -> dict[str, Any]:
    """Pull the sidebar settings for one index folder out of ``epitaxyPrefs``.

    Args:
        prefs: The source machine's ``epitaxyPrefs`` dict.
        index_key: The source ``<accountId>/<orgId>`` key.

    Returns:
        A dict holding the shared keys present, plus ``sections`` if present.
    """
    out: dict[str, Any] = {k: copy.deepcopy(prefs[k]) for k in SHARED_KEYS if k in prefs}
    sections = prefs.get(SECTIONS_KEY)
    if isinstance(sections, dict) and index_key in sections:
        out["sections"] = copy.deepcopy(sections[index_key])
    return out


def _merge_value(src: Any, tgt: Any, keep_ids: set[str]) -> Any:
    """Merge one preference value; the source wins.

    Lists keep the source's items and order, then append target items that are
    session IDs of sessions kept only on the target (so their pins and stars
    survive). Dicts merge key by key, keeping target-only keys.

    Args:
        src: The source value.
        tgt: The target value, or None.
        keep_ids: Session IDs that exist only on the target and are being kept.

    Returns:
        The merged value.
    """
    if isinstance(src, list) and isinstance(tgt, list):
        merged = list(src)
        merged.extend(item for item in tgt if is_valid_session_id(item) and item in keep_ids and item not in merged)
        return merged
    if isinstance(src, dict) and isinstance(tgt, dict):
        out = {k: _merge_value(v, tgt.get(k), keep_ids) for k, v in src.items()}
        out.update({k: v for k, v in tgt.items() if k not in src})
        return out
    return copy.deepcopy(src)


def merge(source: dict[str, Any], target_prefs: dict[str, Any], target_key: str, keep_ids: set[str]) -> dict[str, Any]:
    """Apply the source's sidebar settings to the target's ``epitaxyPrefs``.

    Args:
        source: What ``extract`` returned on the source machine.
        target_prefs: The target's current ``epitaxyPrefs`` (not modified).
        target_key: The target ``<accountId>/<orgId>`` key.
        keep_ids: Target-only session IDs being kept.

    Returns:
        A new ``epitaxyPrefs`` dict.
    """
    prefs = copy.deepcopy(target_prefs)
    for key in SHARED_KEYS:
        if key in source:
            prefs[key] = _merge_value(source[key], target_prefs.get(key), keep_ids)
    if "sections" in source:
        sections = prefs.get(SECTIONS_KEY)
        if not isinstance(sections, dict):
            sections = {}
        sections[target_key] = copy.deepcopy(source["sections"])
        prefs[SECTIONS_KEY] = sections
    return prefs
