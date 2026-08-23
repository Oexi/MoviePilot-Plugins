# _*_ coding: utf-8 _*_
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SENSITIVE_QUERY_KEYS = {
    "apikey",
    "api_key",
    "jackett_apikey",
    "password",
    "token",
}


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
