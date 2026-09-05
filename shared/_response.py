"""Bounded response-body helpers shared by the indexer plugins."""

from __future__ import annotations

import requests
from urllib3.exceptions import ReadTimeoutError


DEFAULT_STREAM_CHUNK_SIZE = 64 * 1024


class ResponseBodyTooLarge(Exception):
    """Raised when a streamed, decompressed response exceeds its byte limit."""


class ResponseReadTimeout(TimeoutError):
    """Raised when a streamed response contains a verified read timeout."""


def _contains_read_timeout(error: BaseException) -> bool:
    """Return whether a requests connection error wraps urllib3 read timeout."""
    pending = [error]
    visited = set()
    while pending:
        candidate = pending.pop()
        candidate_id = id(candidate)
        if candidate_id in visited:
            continue
        visited.add(candidate_id)
        if isinstance(candidate, ReadTimeoutError):
            return True
        if not isinstance(candidate, requests.exceptions.ConnectionError):
            continue
        related = (*candidate.args, candidate.__cause__, candidate.__context__)
        pending.extend(value for value in related if isinstance(value, BaseException))
    return False


def read_limited_response(
    response: object,
    max_bytes: int,
    chunk_size: int = DEFAULT_STREAM_CHUNK_SIZE,
) -> bytes:
    """Read response bytes from ``iter_content`` without exceeding the limit.

    ``requests`` yields decompressed bytes from ``iter_content``.  Counting
    those chunks keeps the limit effective for both compressed and uncompressed
    responses while avoiding a response-wide allocation before validation.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")

    iter_content = getattr(response, "iter_content", None)
    if not callable(iter_content):
        raise TypeError("streaming response must provide iter_content")

    chunks = []
    total = 0
    try:
        for chunk in iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise TypeError("iter_content must yield bytes")
            chunk_length = chunk.nbytes if isinstance(chunk, memoryview) else len(chunk)
            if total + chunk_length > max_bytes:
                raise ResponseBodyTooLarge(max_bytes)
            chunks.append(chunk if isinstance(chunk, bytes) else bytes(chunk))
            total += chunk_length
    except Exception as error:
        if _contains_read_timeout(error):
            raise ResponseReadTimeout() from error
        raise
    return b"".join(chunks)
