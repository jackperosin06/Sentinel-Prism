"""Constants for audit correlation (Story 6.3 — FR33).

``audit_events.run_id`` is NOT NULL for all rows. Pipeline automation uses the
real LangGraph ``run_id``. **Routing rule configuration** mutations from the
admin API use this fixed sentinel so Epic 8 search can group configuration
history without colliding with real runs.
"""

from __future__ import annotations

import uuid

# UUID version 4, variant bits set — arbitrary but stable project constant.
ROUTING_CONFIG_AUDIT_RUN_ID = uuid.UUID("6f3c8e2a-9b1d-4c7e-8a5f-2d0e1b9c4a7d")
