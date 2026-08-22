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
    try:
        if int(status_code) != 200:
            return False
    except (TypeError, ValueError):
        return False
    if not isinstance(text, str) or not text.strip():
        return False
    return "json" not in str(content_type or "").lower()


def redact_url(url: object) -> str:
    """Mask credentials in a URL before it is written to plugin logs."""
    if not isinstance(url, str):
        return ""
    try:
        parsed = urlsplit(url)
        query = urlencode([
            (key, "***" if key.lower() in _SENSITIVE_QUERY_KEYS else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ])
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))
    except (TypeError, ValueError):
        return "<invalid-url>"
