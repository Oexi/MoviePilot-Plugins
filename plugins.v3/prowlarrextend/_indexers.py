# _*_ coding: utf-8 _*_
"""Pure helpers for Prowlarr indexer resources.

The Prowlarr API and the MoviePilot site model use slightly different
representations for an indexer.  This module is the deliberately small,
side-effect-free boundary between the two representations.  In particular,
it does not import MoviePilot, perform HTTP requests, or retain plugin state;
the entry point owns those concerns.
"""

import ast
import copy
import math
import re
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import quote, unquote


PRIVACY_PUBLIC = "public"
PRIVACY_SEMI_PRIVATE = "semi-private"
PRIVACY_PRIVATE = "private"
PRIVACY_UNKNOWN = "unknown"

# Prowlarr's configured indexer id is an integer.  Keeping a conservative
# signed-32-bit bound gives URL construction a cheap, deterministic guard
# against path injection and accidental giant values.  The API itself uses
# positive ids, so zero is not a configured indexer id.
MIN_INDEXER_ID = 1
MAX_INDEXER_ID = 2_147_483_647
_MAX_INDEXER_ID_TEXT_LENGTH = len(str(MAX_INDEXER_ID))


_PRIVACY_ALIASES = {
    "public": PRIVACY_PUBLIC,
    "private": PRIVACY_PRIVATE,
    # Prowlarr returns camelCase ``semiPrivate``.  MoviePilot's existing site
    # vocabulary uses a hyphen, so all accepted spellings normalize to it.
    "semiprivate": PRIVACY_SEMI_PRIVATE,
    "semi-private": PRIVACY_SEMI_PRIVATE,
    "semi_private": PRIVACY_SEMI_PRIVATE,
    "semi private": PRIVACY_SEMI_PRIVATE,
    "semi-public": PRIVACY_SEMI_PRIVATE,
    "semi_public": PRIVACY_SEMI_PRIVATE,
    "semi public": PRIVACY_SEMI_PRIVATE,
    "unknown": PRIVACY_UNKNOWN,
}


def normalize_indexer_id(value: object) -> str:
    """Return a canonical positive Prowlarr id, or ``""`` when invalid.

    API ids are integers.  Integer-looking strings are accepted because
    MoviePilot configuration values and persisted site rows commonly cross a
    JSON/form boundary as strings.  Floats, booleans, signs, decimals, and
    values outside the bounded API range are rejected instead of being
    coerced into a different endpoint.
    """
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        numeric = value
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate or len(candidate) > _MAX_INDEXER_ID_TEXT_LENGTH:
            return ""
        if not re.fullmatch(r"[0-9]+", candidate):
            return ""
        try:
            numeric = int(candidate, 10)
        except (TypeError, ValueError, OverflowError):
            return ""
    else:
        # Do not accept 1.0 or arbitrary objects whose __str__ happens to be
        # numeric.  The API contract is an integer, not a generic number.
        return ""
    if numeric < MIN_INDEXER_ID or numeric > MAX_INDEXER_ID:
        return ""
    return str(numeric)


def parse_indexer_id(value: object) -> Optional[int]:
    """Parse a validated indexer id as an integer for callers needing one."""
    normalized = normalize_indexer_id(value)
    return int(normalized) if normalized else None


def is_valid_indexer_id(value: object) -> bool:
    """Return whether *value* is a bounded positive configured id."""
    return bool(normalize_indexer_id(value))


# This spelling is useful to callers that prefer a verb-style predicate.
validate_indexer_id = is_valid_indexer_id


def normalize_privacy(value: object) -> Optional[str]:
    """Normalize Prowlarr's ``public/private/semiPrivate`` value.

    Unknown values are not guessed as private.  Returning ``None`` for an
    absent/unsupported value lets the caller retain the site's coarse public
    fallback without claiming knowledge it does not have.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    return _PRIVACY_ALIASES.get(text)


def indexer_privacy(value: object) -> Optional[str]:
    """Extract and normalize the privacy value from one API row."""
    if not isinstance(value, Mapping):
        return None
    raw = value.get("privacy")
    if raw is None or not str(raw).strip():
        return None
    return normalize_privacy(raw)


def privacy_label(value: object, public: object = None) -> str:
    """Render a profile privacy value using MoviePilot's stable UI labels."""
    privacy = normalize_privacy(value)
    labels = {
        PRIVACY_PUBLIC: "公开",
        PRIVACY_SEMI_PRIVATE: "半公开",
        PRIVACY_PRIVATE: "私有",
        PRIVACY_UNKNOWN: "未知",
    }
    if privacy in labels:
        return labels[privacy]
    # Older/profile-shaped rows may have only the boolean field.  Do not use
    # generic truthiness: strings such as ``"false"`` are malformed, not
    # evidence of a private/public classification.
    if isinstance(public, bool):
        return "公开" if public else "私有"
    if isinstance(public, int) and not isinstance(public, bool) and public in (0, 1):
        return "公开" if public else "私有"
    return "未知"


def _as_true(value: object) -> bool:
    """Interpret API booleans without the ``bool('false')`` trap."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, float) and math.isfinite(value):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def is_supported_indexer(value: object) -> bool:
    """Return whether a Prowlarr row is eligible for a Torrent site.

    Prowlarr may contain Usenet indexers, disabled indexers, and indexers
    which do not implement search.  All three must stay out of the virtual
    site list, even when a row otherwise looks complete.
    """
    if not isinstance(value, Mapping):
        return False
    if not normalize_indexer_id(value.get("id")):
        return False
    name = str(value.get("name") or "").strip()
    if not name:
        return False
    protocol = str(value.get("protocol") or "").strip().lower()
    return (
        _as_true(value.get("enable"))
        and protocol == "torrent"
        and _as_true(value.get("supportsSearch"))
    )


def parse_indexer_sites(value: object) -> list:
    """Normalize whitelist input to unique canonical numeric id strings.

    The host has persisted this setting as a multi-select list, comma-
    separated text, and stringified Python-style lists.  A small recursion
    limit keeps malformed nested values harmless and deterministic.
    """
    cleaned = []

    def append_value(item: object, depth: int = 0) -> None:
        if depth > 5 or item is None:
            return
        if isinstance(item, Mapping):
            # Be liberal with UI option-shaped values, while never converting
            # arbitrary mappings to a string that could accidentally match.
            if "value" in item:
                append_value(item.get("value"), depth + 1)
            return
        if isinstance(item, (list, tuple, set)):
            for child in item:
                append_value(child, depth + 1)
            return
        if isinstance(item, bool):
            return
        if isinstance(item, int):
            normalized = normalize_indexer_id(item)
            if normalized:
                cleaned.append(normalized)
            return
        text = str(item).strip()
        if not text:
            return
        # Parse list/tuple/set literals before splitting so quoted numeric
        # values are handled as values rather than punctuation fragments.
        if text.startswith(("[", "(", "{")) and text.endswith(("]", ")", "}")):
            try:
                parsed = ast.literal_eval(text)
            except Exception:
                parsed = None
            if isinstance(parsed, (list, tuple, set)):
                append_value(parsed, depth + 1)
                return
        quoted = re.findall(r"[\'\"]([^\'\"]+)[\'\"]", text)
        if quoted:
            for child in quoted:
                append_value(child, depth + 1)
            return
        for child in text.strip("[](){}'\" ").split(","):
            child = child.strip()
            if child:
                normalized = normalize_indexer_id(child)
                if normalized:
                    cleaned.append(normalized)

    append_value(value)
    result = []
    seen = set()
    for item in cleaned:
        normalized = normalize_indexer_id(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def selection_is_explicit(raw_sites: object, explicit: Optional[bool] = None) -> bool:
    """Whether an empty parsed whitelist means "select none"."""
    if explicit:
        return True
    # Any non-empty raw value represents an intentional finite selection,
    # including a stale/invalid value.  This avoids silently turning a bad
    # persisted whitelist into an all-indexers selection.
    return bool(parse_indexer_sites(raw_sites)) or bool(raw_sites)


def apply_indexer_selection(
        indexers: object,
        selected_sites: object,
        explicit: Optional[bool] = None,
) -> list:
    """Apply an exact numeric whitelist without mutating input rows."""
    if not isinstance(indexers, list):
        return []
    selected_ids = parse_indexer_sites(selected_sites)
    if not selected_ids:
        return [] if selection_is_explicit(selected_sites, explicit) else copy.deepcopy(indexers)
    selected = set(selected_ids)
    result = []
    for indexer in indexers:
        if not isinstance(indexer, Mapping):
            continue
        indexer_id = normalize_indexer_id(indexer.get("indexer_id", indexer.get("id")))
        if indexer_id in selected:
            result.append(copy.deepcopy(indexer))
    return result


def indexer_id_from_domain(
        domain: object,
        domain_prefixes: Sequence[str] = ("prowlarr_extend.",),
) -> str:
    """Decode and validate a numeric id from a synthetic virtual domain."""
    value = str(domain or "").strip()
    for prefix in domain_prefixes:
        normalized_prefix = str(prefix or "").strip().lower()
        if normalized_prefix and value.lower().startswith(normalized_prefix):
            return normalize_indexer_id(unquote(value[len(normalized_prefix):]))
    return ""


def is_virtual_site(
        site: object,
        domain: str = "",
        plugin_name: str = "ProwlarrExtend",
        domain_prefixes: Sequence[str] = ("prowlarr_extend.",),
) -> bool:
    """Recognize explicit Prowlarr ownership or a valid virtual domain."""
    if not isinstance(site, Mapping) or not site:
        return False
    marker = str(plugin_name or "").strip().lower()
    markers = {
        str(site.get("plugin") or "").strip().lower(),
        str(site.get("parser") or "").strip().lower(),
    }
    # Explicit markers take precedence over domain inference.  This prevents
    # a record explicitly owned by another parser from being claimed merely
    # because a stale hostname uses our historical prefix.
    if any(markers):
        return bool(marker and marker in markers)
    candidate_domain = domain or str(site.get("domain") or "")
    return bool(indexer_id_from_domain(candidate_domain, domain_prefixes))


def _category_id(value: object) -> str:
    """Normalize a Torznab category id without applying indexer-id bounds."""
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, int):
        return str(value) if value >= 0 else ""
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]+", text):
        return ""
    # Categories are small Torznab integers; this bound protects int() from
    # pathological API input while allowing every standard category range.
    if len(text) > 12:
        return ""
    try:
        numeric = int(text, 10)
    except (TypeError, ValueError, OverflowError):
        return ""
    return str(numeric)


def _category_bucket(category_id: str) -> Optional[str]:
    try:
        numeric = int(category_id, 10)
    except (TypeError, ValueError, OverflowError):
        return None
    if 2000 <= numeric < 3000:
        return "movie"
    if 3000 <= numeric < 4000:
        return "music"
    if 5000 <= numeric < 6000:
        return "tv"
    return None


def category_for_capabilities(capabilities: object) -> dict:
    """Flatten Prowlarr's nested ``capabilities.categories`` tree.

    MoviePilot expects ``{media_type: [{id, cat, desc}, ...]}``.  Both a
    root category and each nested ``subCategories`` entry are retained; this
    is important because Prowlarr category ids such as 2010/5010 are where
    many indexers expose their useful filters.
    """
    if isinstance(capabilities, Mapping):
        categories = capabilities.get("categories")
    else:
        categories = capabilities
    result = {"movie": [], "tv": [], "music": []}
    seen = {key: set() for key in result}

    def children_for(node: Mapping) -> list:
        for key in ("subCategories", "subcategories", "children", "categories"):
            child = node.get(key)
            if isinstance(child, list):
                return child
            if isinstance(child, tuple):
                return list(child)
        return []

    def visit(nodes: object, inherited_bucket: Optional[str] = None) -> None:
        if not isinstance(nodes, (list, tuple)):
            return
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            category_id = _category_id(node.get("id", node.get("ID")))
            bucket = _category_bucket(category_id) or inherited_bucket
            if bucket and category_id and category_id not in seen[bucket]:
                name = str(node.get("name", node.get("Name")) or "").strip()
                name = name or category_id
                result[bucket].append({"id": category_id, "cat": name, "desc": name})
                seen[bucket].add(category_id)
            visit(children_for(node), bucket)

    visit(categories)
    # Preserve the established compact shape for consumers and tests: media
    # keys with no capabilities are omitted rather than populated with empty
    # arrays.  The host treats both forms similarly, but omission matches the
    # Jackett helper and keeps profiles concise.
    return {key: value for key, value in result.items() if value}


def _category_for_caps(caps: object) -> dict:
    """Compatibility spelling for callers that pass a categories list."""
    return category_for_capabilities(caps)


# Keep both descriptive and model-compatible spellings available to a V3
# entry point without making callers depend on a private implementation name.
_category_for_capabilities = category_for_capabilities
is_eligible_indexer = is_supported_indexer


def build_indexer_profiles(
        raw_indexers: object,
        host: str,
        proxy: bool,
        plugin_name: str = "ProwlarrExtend",
        domain_prefix: str = "prowlarr_extend.",
) -> list:
    """Build MoviePilot profiles from eligible Prowlarr API rows.

    Profiles point at each indexer's current Newznab endpoint.  Numeric ids
    are validated before interpolation, and every profile carries explicit
    plugin/parser markers so Prowlarr and Jackett virtual sites can coexist.
    """
    if not isinstance(raw_indexers, list):
        return []
    normalized_host = str(host or "").strip().rstrip("/")
    plugin = str(plugin_name or "ProwlarrExtend").strip() or "ProwlarrExtend"
    prefix = str(domain_prefix or "prowlarr_extend.").strip() or "prowlarr_extend."
    profiles = []
    seen_ids = set()
    for value in raw_indexers:
        if not is_supported_indexer(value):
            continue
        indexer_id = normalize_indexer_id(value.get("id"))
        if not indexer_id or indexer_id in seen_ids:
            continue
        indexer_name = str(value.get("name") or "").strip()
        if not indexer_name:
            continue
        seen_ids.add(indexer_id)
        encoded_id = quote(indexer_id, safe=".-_~")
        privacy = indexer_privacy(value)
        profiles.append({
            "id": f"{plugin}-{indexer_name}",
            "indexer_id": indexer_id,
            "name": f"{plugin}-{indexer_name}",
            "url": f"{normalized_host}/api/v1/indexer/{encoded_id}/newznab",
            "domain": f"{prefix}{encoded_id}",
            "public": privacy == PRIVACY_PUBLIC,
            "privacy": privacy,
            "proxy": bool(proxy),
            "plugin": plugin,
            "parser": plugin,
            "category": category_for_capabilities(value.get("capabilities")),
        })
    return profiles


# Small aliases make the pure contract convenient for entry points while
# retaining the descriptive function names used by the Jackett helper.
build_profiles = build_indexer_profiles
build_indexers = build_indexer_profiles
