# _*_ coding: utf-8 _*_
"""Pure Prowlarr Torznab URL, response, and XML-result helpers.

No helper in this module imports MoviePilot or performs I/O.  HTTP/session
management and conversion into ``TorrentInfo`` stay in the plugin entry
point, while deterministic URL construction, redaction, XML extraction,
numeric coercion, and duplicate policy remain easy to test in isolation.
"""

import copy
import math
import re
import xml.dom.minidom
from collections.abc import Mapping
from typing import Any, Optional
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit


MIN_INDEXER_ID = 1
MAX_INDEXER_ID = 2_147_483_647
_MAX_INDEXER_ID_TEXT_LENGTH = len(str(MAX_INDEXER_ID))

_SENSITIVE_QUERY_KEYS = {
    "apikey",
    "api_key",
    "token",
    "q",
    # These are not part of the Prowlarr API request itself, but accepting
    # them here keeps diagnostics safe when a caller passes a generic URL.
    "password",
    "jackett_apikey",
}


def normalize_indexer_id(value: object) -> str:
    """Return a bounded positive numeric indexer id, or ``""``."""
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
        return ""
    if numeric < MIN_INDEXER_ID or numeric > MAX_INDEXER_ID:
        return ""
    return str(numeric)


def parse_indexer_id(value: object) -> Optional[int]:
    """Parse an id after applying the same validation as URL construction."""
    normalized = normalize_indexer_id(value)
    return int(normalized) if normalized else None


def is_valid_indexer_id(value: object) -> bool:
    """Return whether an id can safely identify a Prowlarr endpoint."""
    return bool(normalize_indexer_id(value))


validate_indexer_id = is_valid_indexer_id


def _base_url(host: object) -> str:
    """Normalize an HTTP(S) host while preserving an optional base path."""
    if host is None:
        return ""
    value = str(host).strip()
    if not value:
        return ""
    if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value):
        value = "http://" + value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return ""
    # Query/fragment on a configured host would make the endpoint ambiguous;
    # rejecting them is safer than silently moving user data into a request.
    if parsed.query or parsed.fragment:
        return ""
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def build_torznab_url(
        host: object,
        indexer_id: object,
        api_key: object = "",
        keyword: object = "",
        cat: object = None,
        *,
        query_type: str = "search",
        params: Optional[Mapping] = None,
        query: Optional[Mapping] = None,
        include_api_key: bool = False,
        **extra: object,
) -> str:
    """Build a Prowlarr per-indexer Newznab URL with encoded query values.

    Prowlarr's current endpoint is ``/api/v1/indexer/{id}/newznab``.  The
    endpoint id is validated as a bounded integer before interpolation; all
    user-controlled query values are encoded with :func:`urlencode`.
    ``cat`` and optional ``params``/keyword arguments are provided for the
    site's category browser and future Torznab parameters.
    """
    base = _base_url(host)
    normalized_id = normalize_indexer_id(indexer_id)
    if not base or not normalized_id:
        return ""
    # Current Prowlarr requests use the ``X-Api-Key`` header.  Keep the
    # positional api_key argument for a uniform entry-point call signature,
    # but do not put it in a URL by default (URLs routinely end up in logs or
    # browser history).  ``include_api_key`` is an explicit opt-in for a
    # caller targeting a Torznab consumer that requires query auth.
    query_values = {
        "t": str(query_type or "search"),
        "q": "" if keyword is None else str(keyword),
    }
    if include_api_key:
        query_values["apikey"] = "" if api_key is None else str(api_key)
    if cat is not None and str(cat).strip():
        query_values["cat"] = cat
    # A mapping in the third positional slot is also accepted as a complete
    # query mapping.  This keeps the helper convenient for callers that do
    # not need API-key/header configuration at all.
    if isinstance(api_key, Mapping) and params is None and query is None:
        params = api_key
    for query_params in (query, params):
        if not isinstance(query_params, Mapping):
            continue
        for key, value in query_params.items():
            if key is None:
                continue
            query_values[str(key)] = value
    # ``category`` is a common caller spelling; do not duplicate it as an
    # unknown URL parameter when ``cat`` was omitted.
    if cat is None and "category" in extra:
        category = extra.pop("category")
        if category is not None and str(category).strip():
            query_values["cat"] = category
    for key, value in extra.items():
        if key == "category":
            continue
        if value is not None:
            query_values[str(key)] = value
    query_string = urlencode(query_values, doseq=True, quote_via=quote_plus)
    return f"{base}/api/v1/indexer/{normalized_id}/newznab?{query_string}"


def build_indexer_torznab_url(*args: object, **kwargs: object) -> str:
    """Descriptive alias for :func:`build_torznab_url`."""
    return build_torznab_url(*args, **kwargs)


def safe_int(value: object) -> int:
    """Convert an integer field, returning zero for malformed values."""
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def safe_float(value: object) -> float:
    """Convert a finite, non-negative float or return ``0.0``."""
    if isinstance(value, bool):
        return 0.0
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return numeric if math.isfinite(numeric) and numeric >= 0 else 0.0


def safe_count(value: object) -> int:
    """Convert a count to a non-negative integer or return zero."""
    if isinstance(value, bool):
        return 0
    try:
        numeric = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return numeric if numeric >= 0 else 0


def safe_float_none(value: object) -> Optional[float]:
    """Convert a finite, non-negative optional float or return ``None``."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return numeric if math.isfinite(numeric) and numeric >= 0 else None


def normalize_imdbid(value: object) -> str:
    """Normalize a valid IMDb title id without inventing an identity."""
    candidate = str(value or "").strip().lower()
    if not re.fullmatch(r"tt[0-9]{7,}", candidate):
        return ""
    if set(candidate[2:]) == {"0"}:
        return ""
    return candidate


def _dom_tag_value(
        node: object,
        tag: str,
        attr: Optional[str] = None,
        default: object = None,
) -> object:
    """Read the first matching DOM tag using stdlib DOM operations only."""
    if node is None or not hasattr(node, "getElementsByTagName"):
        return default
    try:
        elements = node.getElementsByTagName(tag)
    except Exception:
        return default
    if not elements:
        return default
    element = elements[0]
    if attr:
        return element.getAttribute(attr) or default
    first_child = getattr(element, "firstChild", None)
    if first_child is not None and hasattr(first_child, "data"):
        return first_child.data
    return default


def _torznab_attrs(item: object):
    """Yield namespaced Torznab attr elements, accepting prefix variants."""
    if item is None or not hasattr(item, "getElementsByTagName"):
        return
    try:
        elements = item.getElementsByTagName("torznab:attr")
    except Exception:
        elements = []
    yielded = set()
    for element in elements:
        yielded.add(id(element))
        yield element
    # Some XML producers use a default namespace and emit ``attr`` without
    # the ``torznab:`` prefix.  The local-name fallback costs little and keeps
    # extraction independent of a serializer's chosen prefix.
    try:
        all_elements = item.getElementsByTagName("*")
    except Exception:
        all_elements = []
    for element in all_elements:
        if id(element) in yielded:
            continue
        tag_name = str(getattr(element, "tagName", "") or "")
        local_name = str(getattr(element, "localName", "") or "")
        if tag_name.rsplit(":", 1)[-1].lower() != "attr" and local_name.lower() != "attr":
            continue
        namespace = str(getattr(element, "namespaceURI", "") or "")
        if namespace and "torznab" not in namespace.lower():
            continue
        yield element


def extract_torznab_item(item: object) -> dict:
    """Extract stable RSS/Torznab fields from one DOM ``item`` node.

    Numeric values intentionally remain raw strings.  The plugin entry point
    decides how to adapt them to its host model; keeping extraction pure also
    makes malformed fields easy to test without MoviePilot imports.
    """
    fields = {
        "title": _dom_tag_value(item, "title", default=""),
        "enclosure": _dom_tag_value(item, "enclosure", "url", default=""),
        "link": _dom_tag_value(item, "link", default=""),
        "guid": _dom_tag_value(item, "guid", default=""),
        "description": _dom_tag_value(item, "description", default=""),
        "size": _dom_tag_value(item, "size", default=0),
        "page_url": _dom_tag_value(item, "comments", default=""),
        "pubdate": _dom_tag_value(item, "pubDate", default=""),
        "seeders": 0,
        "peers": 0,
        "imdbid": "",
        "infohash": "",
        "grabs": 0,
        "uploadvolumefactor": None,
        "downloadvolumefactor": None,
        "hit_and_run": False,
        "magnet_url": "",
        "labels": [],
    }
    for torznab_attr in _torznab_attrs(item):
        try:
            name = str(torznab_attr.getAttribute("name") or "").strip().lower()
            value = torznab_attr.getAttribute("value")
        except Exception:
            continue
        if name == "seeders":
            fields["seeders"] = value
        elif name == "peers":
            fields["peers"] = value
        elif name == "downloadvolumefactor":
            fields["downloadvolumefactor"] = value
        elif name == "uploadvolumefactor":
            fields["uploadvolumefactor"] = value
        elif name == "hit_and_run":
            fields["hit_and_run"] = str(value).strip().lower() in ("1", "true", "yes")
        elif name == "imdbid":
            fields["imdbid"] = normalize_imdbid(value)
        elif name in ("infohash", "info_hash"):
            fields["infohash"] = str(value).strip()
        elif name == "magneturl":
            fields["magnet_url"] = str(value or "").strip()
        elif name == "grabs":
            fields["grabs"] = value
        elif name in ("label", "tag"):
            label = str(value).strip()
            if label and label not in fields["labels"]:
                fields["labels"].append(label)
    return fields


def _supported_url(value: object, schemes: tuple[str, ...]) -> Optional[str]:
    """Return a URL only when its scheme and HTTP authority are valid."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if scheme not in schemes:
        return None
    if scheme in ("http", "https") and not parsed.netloc:
        return None
    if scheme == "magnet" and not (parsed.query or parsed.path):
        return None
    return candidate


def _looks_like_download_link(value: object) -> bool:
    """Distinguish a Torznab detail page from a direct ``link`` download.

    Torznab's ``enclosure`` is the download field; ``link`` is usually a
    provider detail page.  A link is therefore accepted as a fallback only
    when its path/query carries an unambiguous download hint.  This retains
    protected ``/dl`` links while avoiding accidental promotion of an HTML
    detail page to a torrent URL.
    """
    direct = _supported_url(value, ("http", "https"))
    if not direct:
        return False
    try:
        parsed = urlsplit(direct)
    except ValueError:
        return False
    path = parsed.path.lower().rstrip("/")
    if path.endswith(".torrent") or path.endswith(".magnet"):
        return True
    if "/dl" in path or "/download" in path:
        return True
    query_keys = {key.lower() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)}
    return bool(query_keys.intersection({"download", "downloadurl", "torrent", "torrentfile"}))


def select_torznab_enclosure(
        enclosure: object = None,
        link: object = None,
        magnet_url: object = None,
        guid: object = None,
        download_url: object = None,
) -> str:
    """Select a usable HTTP torrent URL, or fall back to a magnet exactly.

    Detail/comments links, numeric ``files`` attributes, and arbitrary GUIDs
    are never promoted to downloads.  An HTTP(S) enclosure/link is preferred
    over a magnet because it can be fetched and validated by the host.
    """
    for value in (download_url, enclosure):
        direct_url = _supported_url(value, ("http", "https"))
        if direct_url:
            return direct_url
    if _looks_like_download_link(link):
        return str(link).strip()
    for value in (download_url, enclosure, link, magnet_url, guid):
        magnet = _supported_url(value, ("magnet",))
        if magnet:
            return magnet
    return ""


def select_torznab_identity(
        infohash: object,
        guid: object,
        page_url: object,
        enclosure: object,
) -> tuple[str, str]:
    """Choose one stable identity in Torznab's deterministic priority order."""
    for kind, value in (
        ("infohash", infohash),
        ("guid", guid),
        ("page_url", page_url),
        ("enclosure", enclosure),
    ):
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return kind, text.lower()
    return "enclosure", str(enclosure or "").strip().lower()


def is_http_torznab_url(value: object) -> bool:
    """Return whether a value is an HTTP(S) download URL."""
    return bool(_supported_url(value, ("http", "https")))


def should_replace_torznab_duplicate(
        previous_enclosure: object,
        current_enclosure: object,
) -> bool:
    """Prefer an HTTP torrent when it duplicates an earlier magnet."""
    return is_http_torznab_url(current_enclosure) and not is_http_torznab_url(previous_enclosure)


def dedupe_torznab_items(items: object) -> list:
    """Deduplicate extracted item mappings without mutating caller data.

    Identity priority matches :func:`select_torznab_identity`.  When one
    identity appears first as a magnet and later as an HTTP torrent, the
    later usable item replaces the earlier result in place; otherwise first
    occurrence wins.
    """
    if not isinstance(items, (list, tuple)):
        return []
    results = []
    seen = {}
    for value in items:
        if isinstance(value, Mapping):
            item = copy.deepcopy(dict(value))
        elif hasattr(value, "getElementsByTagName"):
            item = extract_torznab_item(value)
        else:
            continue
        enclosure = select_torznab_enclosure(
            enclosure=item.get("enclosure"),
            link=item.get("link"),
            magnet_url=item.get("magnet_url"),
            guid=item.get("guid"),
            download_url=item.get("download_url"),
        )
        if not enclosure:
            continue
        item["enclosure"] = enclosure
        identity = select_torznab_identity(
            item.get("infohash", ""),
            item.get("guid", ""),
            item.get("page_url", item.get("comments", "")),
            enclosure,
        )
        previous = seen.get(identity)
        if previous is None:
            seen[identity] = (len(results), enclosure)
            results.append(item)
        elif should_replace_torznab_duplicate(previous[1], enclosure):
            results[previous[0]] = item
            seen[identity] = (previous[0], enclosure)
    return results


deduplicate_torznab_items = dedupe_torznab_items


def classify_torznab_response(
        status_code: object,
        content_type: object,
        text: object,
) -> str:
    """Classify a response before XML parsing without retaining its body."""
    if isinstance(status_code, bool):
        return "http_error"
    try:
        status = int(status_code)
    except (TypeError, ValueError, OverflowError):
        return "http_error"
    if status != 200:
        return "http_error"
    if isinstance(text, bytes):
        try:
            body = text.decode("utf-8", errors="replace")
        except Exception:
            body = ""
    elif isinstance(text, str):
        body = text
    else:
        body = ""
    if not body.strip():
        return "empty"
    content = str(content_type or "").lower()
    if "json" in content or body.lstrip().startswith(("{", "[")):
        return "json"
    return "ok"


def is_usable_torznab_response(
        status_code: object,
        content_type: object,
        text: object,
) -> bool:
    """Return whether status/body are suitable for Torznab XML parsing."""
    return classify_torznab_response(status_code, content_type, text) == "ok"


def redact_url(url: object) -> str:
    """Mask API credentials, tokens, and search terms in a diagnostic URL."""
    if not isinstance(url, str):
        return ""
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        if hostname and ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = hostname
        try:
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
        except ValueError:
            return "<invalid-url>"
        query = urlencode([
            (key, "***" if key.lower() in _SENSITIVE_QUERY_KEYS else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ])
        # Credentials in userinfo are intentionally dropped with the rest of
        # netloc.  Keep the fragment for ordinary diagnostics; a fragment is
        # not sent in HTTP requests and cannot expose the query credentials.
        return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))
    except (TypeError, ValueError):
        return "<invalid-url>"


# Concise aliases used by a few callers/tests.
build_newznab_url = build_torznab_url
build_indexer_url = build_torznab_url
build_search_url = build_torznab_url
build_prowlarr_torznab_url = build_torznab_url
classify_response = classify_torznab_response
