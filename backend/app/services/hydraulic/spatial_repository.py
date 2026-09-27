"""Revision-scoped spatial reads for PostGIS and portable hydraulic profiles."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy import text

from app.services.hydraulic.geometry import (
    BoundingBox,
    EntityType,
    SpatialFilterReport,
    build_spatial_query_plan,
    filter_rows_by_bbox,
)


class HydraulicSpatialRepository:
    """Execute one immutable-revision bbox query through the selected profile.

    The session is intentionally duck-typed to keep this repository usable with
    SQLAlchemy's async session while unit tests can provide a minimal executor.
    Both profiles bind the query to ``network_id`` and ``network_revision_id``;
    callers cannot retrieve table-level cross-network rows.
    """

    def __init__(
        self,
        backend: Literal["postgis", "portable"],
        session: Any,
    ) -> None:
        if backend not in {"postgis", "portable"}:
            raise ValueError("spatial backend must be 'postgis' or 'portable'")
        self.backend = backend
        self.session = session

    async def list_by_bbox(
        self,
        *,
        entity_type: EntityType,
        bbox: BoundingBox | str,
        network_id: str | uuid.UUID,
        network_revision_id: str | uuid.UUID,
    ) -> SpatialFilterReport:
        plan = build_spatial_query_plan(
            self.backend,
            entity_type,
            bbox,
            network_id=network_id,
            network_revision_id=network_revision_id,
        )
        statement = text(
            f"SELECT * FROM {plan.table} "  # table comes from a fixed mapping
            f"WHERE {plan.where_sql} ORDER BY {plan.order_by}"
        )
        result = await self.session.execute(statement, dict(plan.parameters))
        rows = tuple(dict(row) for row in result.mappings().all())
        if self.backend == "portable":
            return filter_rows_by_bbox(
                rows,
                plan.bbox,
                entity_type=entity_type,
            )
        return SpatialFilterReport(rows, (), ())


__all__ = ["HydraulicSpatialRepository"]
