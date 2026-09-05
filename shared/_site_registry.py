# _*_ coding: utf-8 _*_
"""Thin adapter around MoviePilot's internal site persistence/event APIs.

The plugin entry points deliberately depend on this tiny surface rather than
importing ``app.db`` directly.  If MoviePilot later exposes a stable site
registry SDK, only this module needs to change.
"""

from __future__ import annotations

from typing import Any


class SiteRegistryAdapter:
    """Minimal persistence/event surface used by virtual-site plugins."""

    def __init__(self, site_oper: object, eventmanager: object, event_type: object):
        self._site_oper = site_oper
        self._eventmanager = eventmanager
        self._event_type = event_type

    def list(self):
        return self._site_oper.list()

    def get_by_domain(self, domain: str):
        return self._site_oper.get_by_domain(domain)

    def add(self, **payload: Any):
        return self._site_oper.add(**payload)

    def update(self, site_id: object, payload: dict):
        return self._site_oper.update(site_id, payload)

    def delete(self, site_id: object):
        return self._site_oper.delete(site_id)

    def notify_deleted(self, site_id: object):
        return self._eventmanager.send_event(
            self._event_type.SiteDeleted,
            {"site_id": site_id},
        )

    def notify_updated(self, domain: str):
        return self._eventmanager.send_event(
            self._event_type.SiteUpdated,
            {"domain": domain},
        )


def open_site_registry() -> SiteRegistryAdapter:
    """Resolve current MoviePilot internals lazily at the side-effect boundary."""
    from app.db.oper.site import SiteOper
    from app.schemas.types import EventType
    from app.sdk.events import eventmanager

    return SiteRegistryAdapter(SiteOper(), eventmanager, EventType)
