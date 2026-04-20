"""Mock routing rule evaluation (Epic 5 — Story 5.1). Delivery lives in ``notifications``."""

from sentinel_prism.services.routing.resolve import (
    RoutingRuleView,
    resolve_routing_decision,
)

__all__ = ["RoutingRuleView", "resolve_routing_decision"]
