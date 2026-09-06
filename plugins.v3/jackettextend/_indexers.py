# _*_ coding: utf-8 _*_
"""Pure Jackett indexer/profile helpers.

This module deliberately contains no MoviePilot imports, network calls, or
plugin state.  The plugin keeps synchronization, caching, and configuration
publication in ``__init__.py`` and delegates only deterministic transformations
here so they can be tested in isolation.
"""

import ast
import copy
import hashlib
import re
from typing import Mapping, Optional, Sequence
from urllib.parse import quote, unquote


PRIVACY_PUBLIC = "public"
PRIVACY_SEMI_PRIVATE = "semi-private"
PRIVACY_PRIVATE = "private"
PRIVACY_UNKNOWN = "unknown"

_PRIVACY_ALIASES = {
    PRIVACY_PUBLIC: PRIVACY_PUBLIC,
    PRIVACY_SEMI_PRIVATE: PRIVACY_SEMI_PRIVATE,
    "semi_private": PRIVACY_SEMI_PRIVATE,
    "semi private": PRIVACY_SEMI_PRIVATE,
    # Keep compatibility with older integrations that used the alternate
    # spelling; Jackett's current definitions use ``semi-private``.
    "semi-public": PRIVACY_SEMI_PRIVATE,
    "semi_public": PRIVACY_SEMI_PRIVATE,
    "semi public": PRIVACY_SEMI_PRIVATE,
    PRIVACY_PRIVATE: PRIVACY_PRIVATE,
    PRIVACY_UNKNOWN: PRIVACY_UNKNOWN,
}

_CATEGORY_RANGES = (
    ("movie", 2000, 3000),
    ("music", 3000, 4000),
    ("tv", 5000, 6000),
)


def build_instance_domain_prefix(historical_prefix: object, instance_id: object) -> str:
    """为虚拟实例生成稳定且不覆盖源实例的合成域名前缀。

    源实例继续使用已有前缀；分身使用运行实例 ID 的可读片段和摘要，既能
    在日志/站点列表中定位实例，也能把超过 DNS 标签长度的合法实例 ID 安全
    地压缩到固定范围。摘要还避免了仅截断前缀导致的同名空间碰撞。
    """
    base = str(historical_prefix or "").strip().rstrip(".").lower() or "virtual"
    identity = str(instance_id or "").strip()
    identity_slug = re.sub(r"[^a-z0-9]+", "-", identity.lower()).strip("-")
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    namespace = f"{identity_slug[:24] or 'instance'}-{digest}"
    return f"{base}_{namespace}."


def normalize_privacy(value: object) -> Optional[str]:
    """Normalize a Jackett privacy/type value to a known category.

    Jackett's configured-indexer endpoint calls this field ``type`` and
    reports ``public``, ``semi-private`` or ``private``.  ``privacy`` is
    accepted as a compatibility fallback for callers/older response shapes;
    values outside the known vocabulary are never guessed as private.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    return _PRIVACY_ALIASES.get(text)


def indexer_privacy(value: object) -> Optional[str]:
    """Extract a reliable privacy value from one Jackett indexer row.

    ``type`` is authoritative for the current Jackett endpoint.  The
    historical ``privacy`` key is used only when ``type`` is absent/empty.
    An explicit ``unknown`` value is preserved for the UI.  Unsupported or
    absent values return ``None`` so the caller can use the existing
    ``public`` fallback instead of inventing a more precise type.
    """
    if not isinstance(value, Mapping):
        return None
    for key in ("type", "privacy"):
        raw = value.get(key)
        if raw is None or not str(raw).strip():
            continue
        return normalize_privacy(raw)
    return None


def privacy_label(value: object, public: object = None) -> str:
    """Render a profile privacy value using the fixed Chinese UI vocabulary."""
    privacy = normalize_privacy(value)
    labels = {
        PRIVACY_PUBLIC: "公开",
        PRIVACY_SEMI_PRIVATE: "半公开",
        PRIVACY_PRIVATE: "私有",
        PRIVACY_UNKNOWN: "未知",
    }
    if privacy in labels:
        return labels[privacy]
    # Profiles from older/cached data may not carry privacy metadata.  The
    # boolean is an intentionally coarse fallback and never implies
    # semi-private precision.  Integer values are accepted for site rows
    # loaded from the host DB (0/1 are its public-field representation).
    if isinstance(public, bool):
        return "公开" if public else "私有"
    if isinstance(public, int) and public in (0, 1):
        return "公开" if public else "私有"
    return "未知"


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
    candidate_domain = domain or str(site.get("domain") or "")
    return bool(indexer_id_from_domain(candidate_domain, domain_prefixes))


def _category_for_caps(caps: object) -> dict:
    category = {}
    for cap in caps if isinstance(caps, (list, tuple)) else []:
        if not isinstance(cap, Mapping):
            continue
        cap_id = str(cap.get("ID", "")).strip()
        if not re.fullmatch(r"[0-9]+", cap_id):
            continue
        cap_name = str(cap.get("Name") or "").strip() or cap_id
        entry = {"id": cap_id, "cat": cap_name, "desc": cap_name}
        try:
            numeric_id = int(cap_id)
        except ValueError:
            continue
        for category_key, lower_bound, upper_bound in _CATEGORY_RANGES:
            if lower_bound <= numeric_id < upper_bound:
                category.setdefault(category_key, []).append(entry)
                break
    return category


def build_indexer_profiles(raw_indexers: object,
                           host: str,
                           proxy: bool,
                           plugin_name: str = "JackettExtend",
                           domain_prefix: str = "jackett_extend.",
                           owner_id: Optional[str] = None) -> list:
    """Build host-facing profiles from a Jackett indexer response.

    Invalid rows are ignored and special IDs are URL/domain encoded while the
    exact ID remains available in ``indexer_id`` for filtering and API calls.
    """
    if not isinstance(raw_indexers, list):
        return []
    normalized_host = str(host or "").rstrip("/")
    owner = str(owner_id or plugin_name or "JackettExtend").strip() or "JackettExtend"
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
        privacy = indexer_privacy(value)
        if privacy is None:
            # Preserve the established boolean semantics while accepting a
            # legacy/profile-shaped row that already carries ``public``.
            public = value.get("public") if isinstance(value.get("public"), bool) else False
        else:
            public = privacy == PRIVACY_PUBLIC
        profiles.append({
            "id": f"{plugin_name}-{indexer_name}",
            "indexer_id": indexer_id,
            "name": f"{plugin_name}-{indexer_name}",
            "url": f"{normalized_host}/api/v2.0/indexers/{encoded_id}/results/torznab/",
            "domain": f"{domain_prefix}{encoded_id}",
            "public": public,
            "privacy": privacy,
            "proxy": bool(proxy),
            # Keep explicit ownership markers on every injected profile.
            "plugin": owner,
            "parser": owner,
            "category": _category_for_caps(value.get("caps")),
        })
    return profiles
