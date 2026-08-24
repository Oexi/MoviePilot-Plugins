# _*_ coding: utf-8 _*_
"""Pure Jackett indexer/profile helpers.

This module deliberately contains no MoviePilot imports, network calls, or
plugin state.  The plugin keeps synchronization, caching, and configuration
publication in ``__init__.py`` and delegates only deterministic transformations
here so they can be tested in isolation.
"""

import ast
import copy
import re
from typing import Mapping, Optional, Sequence
from urllib.parse import quote, unquote


def parse_indexer_sites(value: object) -> list:
    """Normalize UI/API/legacy whitelist values into lower-case IDs.

    MoviePilot has persisted this setting as a multi-select list, a
    comma-separated string, and stringified/nested Python-style lists.  The
    bounded recursion keeps malformed values harmless while retaining the
    historical accepted forms.
    """
    cleaned = []

    def append_value(item: object, depth: int = 0) -> None:
        if depth > 5 or item is None:
            return
        if isinstance(item, (list, tuple, set)):
            for child in item:
                append_value(child, depth + 1)
            return
        text = str(item).strip()
        if not text:
            return
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = ast.literal_eval(text)
            except Exception:
                parsed = None
            if isinstance(parsed, (list, tuple)):
                append_value(parsed, depth + 1)
                return
        quoted = re.findall(r"[\'\"]([^\'\"]+)[\'\"]", text)
        if quoted:
            for child in quoted:
                append_value(child, depth + 1)
            return
        for child in text.strip("[]'\" ").split(","):
            child = child.strip()
            if child:
                cleaned.append(child)

    append_value(value)
    result = []
    seen = set()
    for item in cleaned:
        normalized = str(item).strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def selection_is_explicit(raw_sites: object, explicit: Optional[bool] = None) -> bool:
    """Return whether an empty parsed selection means a finite whitelist."""
    if explicit:
        return True
    # A legacy/object-level fixture may not carry the explicit flag.  A
    # non-empty raw value remains unambiguously a finite selection.
    return bool(parse_indexer_sites(raw_sites)) or bool(raw_sites)


def apply_indexer_selection(
        indexers: object,
        selected_sites: object,
        explicit: Optional[bool] = None,
) -> list:
    """Apply a canonical whitelist without turning stale-all into all."""
    if not isinstance(indexers, list):
        return []
    selected_ids = parse_indexer_sites(selected_sites)
    if not selected_ids:
        return [] if selection_is_explicit(selected_sites, explicit) else copy.deepcopy(indexers)
    selected = set(selected_ids)
    return [
        copy.deepcopy(indexer) for indexer in indexers
        if isinstance(indexer, Mapping)
        and str(indexer.get("indexer_id") or "").strip().lower() in selected
    ]


def indexer_id_from_domain(domain: object,
                           domain_prefixes: Sequence[str] = ("jackett_extend.",)) -> str:
    """Decode an indexer ID from a historical synthetic domain."""
    value = str(domain or "").strip()
    for prefix in domain_prefixes:
        normalized_prefix = str(prefix or "").lower()
        if normalized_prefix and value.lower().startswith(normalized_prefix):
            return unquote(value[len(normalized_prefix):])
    return ""


def is_virtual_site(site: object,
                    domain: str = "",
                    plugin_name: str = "JackettExtend",
                    domain_prefixes: Sequence[str] = ("jackett_extend.",)) -> bool:
    """Recognize plugin-marked profiles and historical virtual domains."""
    if not isinstance(site, Mapping) or not site:
        return False
    marker = str(plugin_name or "").strip().lower()
    markers = {
        str(site.get("plugin") or "").strip().lower(),
        str(site.get("parser") or "").strip().lower(),
    }
    # Explicit markers have precedence over legacy domain inference.  This
    # prevents a record explicitly owned by another parser from being routed
    # solely because an old synthetic hostname happens to remain.
    if any(markers):
        return bool(marker and marker in markers)
    return bool(indexer_id_from_domain(domain, domain_prefixes))


def _category_for_caps(caps: object) -> dict:
    category = {}
    for cap in caps if isinstance(caps, (list, tuple)) else []:
        if not isinstance(cap, Mapping):
            continue
        cap_id = str(cap.get("ID", "")).strip()
        if not cap_id:
            continue
        cap_name = str(cap.get("Name") or "").strip() or cap_id
        entry = {"id": cap_id, "cat": cap_name, "desc": cap_name}
        if cap_id.startswith("2000"):
            category.setdefault("movie", []).append(entry)
        elif cap_id.startswith("5000"):
            category.setdefault("tv", []).append(entry)
        elif cap_id.startswith("3000"):
            category.setdefault("music", []).append(entry)
    return category


def build_indexer_profiles(raw_indexers: object,
                           host: str,
                           proxy: bool,
                           plugin_name: str = "JackettExtend",
                           domain_prefix: str = "jackett_extend.") -> list:
    """Build host-facing profiles from a Jackett indexer response.

    Invalid rows are ignored and special IDs are URL/domain encoded while the
    exact ID remains available in ``indexer_id`` for filtering and API calls.
    """
    if not isinstance(raw_indexers, list):
        return []
    normalized_host = str(host or "").rstrip("/")
    profiles = []
    for value in raw_indexers:
        if not isinstance(value, Mapping):
            continue
        indexer_id = value.get("id")
        indexer_name = value.get("name")
        if not indexer_id or not indexer_name:
            continue
        indexer_id = str(indexer_id).strip()
        if not indexer_id:
            continue
        encoded_id = quote(indexer_id, safe=".-_~")
        privacy = str(value.get("privacy") or "").strip().lower()
        profiles.append({
            "id": f"{plugin_name}-{indexer_name}",
            "indexer_id": indexer_id,
            "name": f"{plugin_name}-{indexer_name}",
            "url": f"{normalized_host}/api/v2.0/indexers/{encoded_id}/results/torznab/",
            "domain": f"{domain_prefix}{encoded_id}",
            "public": privacy == "public",
            "proxy": bool(proxy),
            # Keep explicit ownership markers on every injected profile.
            "plugin": plugin_name,
            "parser": plugin_name,
            "category": _category_for_caps(value.get("caps")),
        })
    return profiles
