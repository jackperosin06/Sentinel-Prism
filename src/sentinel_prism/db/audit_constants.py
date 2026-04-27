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

# Story 7.3 — classification threshold / prompt applies (distinct from routing).
CLASSIFICATION_CONFIG_AUDIT_RUN_ID = uuid.UUID("8a1d4f2e-6c9b-4e1a-9f3d-7b2c5e8d0a1f")

# Story 7.4 — golden-set / evaluation policy applies (distinct from pipeline runs).
GOLDEN_SET_CONFIG_AUDIT_RUN_ID = uuid.UUID("2b7c9e1f-4a8d-4c2b-9e0f-1a3b5d7f9e0c")
