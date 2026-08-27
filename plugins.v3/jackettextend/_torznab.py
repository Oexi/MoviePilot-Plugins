# _*_ coding: utf-8 _*_
import math
import re
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SENSITIVE_QUERY_KEYS = {
    "apikey",
    "api_key",
    "jackett_apikey",
    "password",
    "token",
    # Search terms may contain titles, names, or other user-provided
    # sensitive text.  Keep the key visible for diagnostics but never echo
    # its value in a log URL.
    "q",
}


def safe_int(value: object) -> int:
    """Convert a value to ``int`` while preserving the parser's zero fallback."""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def safe_float(value: object) -> float:
    """Convert a value to a finite, non-negative float or return ``0.0``."""
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return numeric if math.isfinite(numeric) and numeric >= 0 else 0.0


def safe_count(value: object) -> int:
    """Convert a count to a non-negative integer or return ``0``."""
    try:
        numeric = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return numeric if numeric >= 0 else 0


def safe_float_none(value: object) -> Optional[float]:
    """Convert a promotion factor to a finite non-negative float or ``None``."""
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return numeric if math.isfinite(numeric) and numeric >= 0 else None


def normalize_imdbid(value: object) -> str:
    """Normalize a valid IMDb title id without creating an invalid identity."""
    candidate = str(value or "").strip().lower()
    if not re.fullmatch(r"tt[0-9]{7,}", candidate):
        return ""
    if set(candidate[2:]) == {"0"}:
        return ""
    return candidate


def _dom_tag_value(node: object, tag: str, attr: Optional[str] = None,
                   default: object = None) -> object:
    """Read the first matching DOM tag using only stdlib DOM operations."""
    elements = node.getElementsByTagName(tag)
    if not elements:
        return default
    element = elements[0]
    if attr:
        return element.getAttribute(attr) or default
    # Match MoviePilot's historical DomUtils.tag_value contract: return the
    # first child's data, which intentionally supports both TEXT_NODE and
    # CDATA_SECTION_NODE and does not concatenate later siblings.
    first_child = element.firstChild
    if first_child:
        return first_child.data
    return default


def extract_torznab_item(item: object) -> dict:
    """Extract deterministic RSS and ``torznab:attr`` fields from one item.

    The result intentionally contains raw numeric/date strings.  Numeric
    coercion and date normalization belong to the entry point because those
    operations use the host's ``TorrentInfo`` and ``StringUtils`` contracts.
    Attribute values follow the historical parser semantics: scalar values
    use the last occurrence, while labels retain first-seen order and are
    de-duplicated.
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

    for torznab_attr in item.getElementsByTagName("torznab:attr"):
        name = torznab_attr.getAttribute("name")
        value = torznab_attr.getAttribute("value")
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
            fields["magnet_url"] = value
        elif name == "grabs":
            fields["grabs"] = value
        elif name in ("label", "tag"):
            label = str(value).strip()
            if label and label not in fields["labels"]:
                fields["labels"].append(label)

    return fields


def select_torznab_identity(infohash: object, guid: object,
                            page_url: object, enclosure: object) -> tuple[str, str]:
    """Choose one stable identity for a parsed item in historical priority order."""
    identity_values = (
        ("infohash", infohash),
        ("guid", guid),
        ("page_url", page_url),
        ("enclosure", enclosure),
    )
    for kind, value in identity_values:
        if isinstance(value, str) and value.strip():
            return kind, value.strip().lower()
    return "enclosure", str(enclosure).strip().lower()


def is_http_torznab_url(value: object) -> bool:
    """Return whether an enclosure uses an HTTP(S) download scheme."""
    return str(value).strip().lower().startswith(("http://", "https://"))


def should_replace_torznab_duplicate(previous_enclosure: object,
                                     current_enclosure: object) -> bool:
    """Prefer an HTTP torrent when it duplicates an earlier magnet result."""
    return is_http_torznab_url(current_enclosure) and not is_http_torznab_url(previous_enclosure)


def classify_torznab_response(status_code: object, content_type: object,
                              text: object) -> str:
    """Classify a Torznab response without retaining its body.

    The category is intentionally small and stable so callers can expose a
    useful diagnostic (or log line) without echoing an API response that may
    contain credentials or provider-specific details.
    """
    try:
        status = int(status_code)
    except (TypeError, ValueError):
        return "http_error"
    if status != 200:
        return "http_error"
    if not isinstance(text, str) or not text.strip():
        return "empty"
    if "json" in str(content_type or "").lower():
        return "json"
    return "ok"


def _supported_url(value: object, schemes: tuple[str, ...]) -> Optional[str]:
    """Return a Torznab URL only when its scheme is explicitly supported."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme.lower() not in schemes:
        return None
    if parsed.scheme.lower() in ("http", "https") and not parsed.netloc:
        return None
    return candidate


def select_torznab_enclosure(
        enclosure: object = None,
        link: object = None,
        magnet_url: object = None,
        guid: object = None,
) -> str:
    """
    Select a download value using only fields defined by the Torznab result.

    Jackett turns an indexer's HTTP download link into its protected ``/dl``
    URL before emitting Torznab XML.  Such a link can be downloaded and
    validated by MoviePilot's TorrentHelper, so it takes precedence over a
    magnet.  A magnet contains no file list; when it is the only option it is
    preserved verbatim for download flows that support magnets.

    Detail/comments URLs and numeric ``files`` attributes are deliberately not
    inputs: neither is a portable torrent metadata endpoint or a file path.
    """
    for value in (enclosure, link):
        direct_url = _supported_url(value, ("http", "https"))
        if direct_url:
            return direct_url

    for value in (enclosure, link, magnet_url, guid):
        magnet = _supported_url(value, ("magnet",))
        if magnet:
            return magnet

    return ""


def is_usable_torznab_response(status_code: object, content_type: object, text: object) -> bool:
    """Reject empty, unauthorized, and JSON responses before XML parsing."""
    return classify_torznab_response(status_code, content_type, text) == "ok"


def redact_url(url: object) -> str:
    """Mask credentials in a URL before it is written to plugin logs."""
    if not isinstance(url, str):
        return ""
    try:
        parsed = urlsplit(url)
        # URL userinfo is not needed for diagnostics and must never be
        # copied into logs.  Keep only the host/port portion of netloc.
        hostname = parsed.hostname or ""
        if hostname and ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = hostname
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        query = urlencode([
            (key, "***" if key.lower() in _SENSITIVE_QUERY_KEYS else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ])
        return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))
    except (TypeError, ValueError):
        return "<invalid-url>"
