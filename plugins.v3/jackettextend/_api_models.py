"""Pydantic response models for the JackettExtend plugin APIs."""

from pydantic import BaseModel


class JackettSyncStatus(BaseModel):
    """Nested synchronization state returned by the status endpoint."""

    fetch_ok: bool
    ready: bool
    last_ok: bool
    last_at: float | None = None


class JackettStatusResponse(BaseModel):
    """The raw JSON payload returned by ``/status``."""

    enabled: bool
    configured: bool
    connected: bool
    sync: JackettSyncStatus
    indexer_count: int
    selected_count: int
    last_error: str | None = None
    last_error_at: float | None = None
    last_search_error: str | None = None
    last_search_error_at: float | None = None
    probe_error: str | None = None
    probe_error_at: float | None = None


class JackettTestResponse(JackettStatusResponse):
    """The raw JSON payload returned by ``/test``."""

    ok: bool
