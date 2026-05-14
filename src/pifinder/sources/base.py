from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import EnrichmentResult, FirmRecord


@runtime_checkable
class DiscoverySource(Protocol):
    """Sources that produce new firms from a geography + query."""

    name: str

    async def discover(
        self, *, location: str, radius_meters: int, query: str
    ) -> list[FirmRecord]: ...


@runtime_checkable
class EnrichmentSource(Protocol):
    """Sources that add data to an already-known firm."""

    name: str

    async def enrich(self, firm: FirmRecord) -> EnrichmentResult: ...
