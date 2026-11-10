"""Explained reorder suggestions (US6.1) and human PO decisions (US6.2)."""

import math
from datetime import timedelta
from typing import Any, Optional

from ..domain.enums import MovementReason, Permission, PurchaseDecisionType, PurchaseOrderStatus
from ..domain.models import PurchaseDecision, PurchaseOrder, PurchaseOrderLine, User
from ..errors import InvalidTransitionError, ValidationError
from .context import Service, newest_first
from .validation import optional_text, require_int, require_text

P = Permission
USAGE_WINDOW_DAYS = 30
REVIEW_PERIOD_DAYS = 7
FORMULA = ("suggested = ceil(avg daily usage × (lead time + review period) + safety stock "
           "− (on hand + on order)); shown when on hand + on order ≤ reorder point")


class PurchasingService(Service):
    def __init__(self, ctx, inventory):
        super().__init__(ctx)
        self._inventory = inventory

    # ---- suggestions (US6.1) --------------------------------------------
    def calculate(self, product_id: str) -> dict[str, Any]:
        """Every value used in the reorder calculation for one product."""
        product = self.repos.products.get(product_id)
        now = self.clock.now()
        since = now - timedelta(days=USAGE_WINDOW_DAYS)
        used = -sum(
            m.delta for m in self.repos.movements.for_product_since(product.id, since)
            if m.reason in (MovementReason.DISPENSE, MovementReason.SALE)
        )
        avg_daily = used / USAGE_WINDOW_DAYS
        on_hand = self._inventory.on_hand(product.id)
        on_order = sum(
            line.quantity
            for po in self.repos.purchase_orders.with_status(PurchaseOrderStatus.APPROVED)
            for line in po.lines if line.product_id == product.id
        )
        pending = sum(
            line.quantity
            for po in self.repos.purchase_orders.with_status(PurchaseOrderStatus.PENDING_APPROVAL)
            for line in po.lines if line.product_id == product.id
        )
        position = on_hand + on_order
        target = avg_daily * (product.lead_time_days + REVIEW_PERIOD_DAYS) + product.safety_stock
        triggered = position <= product.reorder_point
        suggested = max(1, math.ceil(target - position)) if triggered else 0
        return {
            "product_id": product.id,
            "product": product.name,
            "usage_window_days": USAGE_WINDOW_DAYS,
            "units_used_in_window": used,
            "avg_daily_usage": round(avg_daily, 2),
            "lead_time_days": product.lead_time_days,
            "review_period_days": REVIEW_PERIOD_DAYS,
            "safety_stock": product.safety_stock,
            "reorder_point": product.reorder_point,
            "on_hand": on_hand,
            "on_order": on_order,
            "pending_approval": pending,
            "stock_position": position,
            "target_stock": round(target, 2),
            "triggered": triggered,
            "suggested_quantity": suggested,
            "unit_cost_cents": product.unit_cost_cents,
            "estimated_cost_cents": suggested * product.unit_cost_cents,
            "formula": FORMULA,
            "explanation": (
                f"{used} units used in {USAGE_WINDOW_DAYS} days = {avg_daily:.2f}/day; "
                f"{avg_daily:.2f} × ({product.lead_time_days} + {REVIEW_PERIOD_DAYS}) + {product.safety_stock} safety "
                f"= {target:.2f} target; minus position {position} ({on_hand} on hand + {on_order} on order)"
                + (f" → order {suggested}" if triggered else
                   f" → no order: position {position} is above reorder point {product.reorder_point}")
            ),
        }

    def suggestions(self, actor: User, include_all: bool = False) -> list[dict[str, Any]]:
        self.access.require(actor, P.PURCHASE_VIEW)
        rows = [self.calculate(p.id) for p in self.repos.products.list()]
        if not include_all:
            rows = [r for r in rows if r["triggered"]]
        return sorted(rows, key=lambda r: (-int(r["triggered"]), r["product"]))

    # ---- purchase orders (US6.2) ----------------------------------------
    def create_purchase_order(self, actor: User, product_ids: Optional[list[str]] = None,
                              supplier: str = "Default supplier") -> PurchaseOrder:
        """Draft a PO from current suggestions. It always needs a decision."""
        self.access.require(actor, P.PURCHASE_VIEW)
        supplier = require_text(supplier, "supplier", 80)
        already_pending = {
            line.product_id
            for po in self.repos.purchase_orders.with_status(PurchaseOrderStatus.PENDING_APPROVAL)
            for line in po.lines
        }
        rows = [self.calculate(pid) for pid in product_ids] if product_ids else self.suggestions(actor)
        lines = []
        for row in rows:
            if not row["triggered"] or row["product_id"] in already_pending:
                continue
            lines.append(PurchaseOrderLine(
                product_id=row["product_id"], product_name=row["product"],
                suggested_quantity=row["suggested_quantity"], quantity=row["suggested_quantity"],
                unit_cost_cents=row["unit_cost_cents"],
                basis={k: v for k, v in row.items() if k not in ("product_id", "product")},
            ))
        if not lines:
            raise ValidationError("nothing to order: no selected product needs reordering or all are already pending")
        po = PurchaseOrder(
            id=f"PO-{self.repos.counters.next('purchase_order'):04d}", supplier=supplier, lines=lines,
            status=PurchaseOrderStatus.PENDING_APPROVAL, created_by=actor.id, created_at=self.clock.now(),
        )
        self.repos.purchase_orders.add(po)
        self.audit.record(actor, "PURCHASE_ORDER_DRAFTED", "PurchaseOrder", po.id,
                          {"lines": [[l.product_name, l.suggested_quantity] for l in lines]})
        return po

    def list_purchase_orders(self, actor: User, status: Optional[str] = None) -> list[PurchaseOrder]:
        self.access.require(actor, P.PURCHASE_VIEW)
        orders = self.repos.purchase_orders.list()
        if status:
            orders = [o for o in orders if o.status.value == str(status).upper()]
        return newest_first(orders, lambda o: o.created_at)

    def approve(self, actor: User, po_id: str, quantities: Optional[dict[str, Any]] = None,
                reason: Optional[str] = None) -> PurchaseOrder:
        """Approve as suggested, or with modified quantities (reason required)."""
        self.access.require(actor, P.PURCHASE_DECIDE)
        po = self._pending(po_id)
        reason = optional_text(reason, "reason", 500)
        changes = []
        for product_id, raw_qty in (quantities or {}).items():
            line = next((l for l in po.lines if l.product_id == product_id), None)
            if line is None:
                raise ValidationError(f"product {product_id} is not on {po.id}")
            qty = require_int(raw_qty, f"quantity for {line.product_name}", 0, 100_000)
            if qty != line.quantity:
                changes.append({"product_id": product_id, "product": line.product_name,
                                "suggested": line.suggested_quantity, "from": line.quantity, "to": qty})
                line.quantity = qty
        if changes and not reason:
            raise ValidationError("a reason is required when changing suggested quantities")
        if all(l.quantity == 0 for l in po.lines):
            raise ValidationError("all quantities are zero; reject the order instead")
        decision = PurchaseDecisionType.MODIFIED if changes else PurchaseDecisionType.APPROVED
        po.status = PurchaseOrderStatus.APPROVED
        po.decision = PurchaseDecision(decision=decision, by=actor.id, at=self.clock.now(), reason=reason, changes=changes)
        self.repos.purchase_orders.update(po)
        self.audit.record(actor, f"PURCHASE_ORDER_{decision.value}", "PurchaseOrder", po.id,
                          {"reason": reason, "changes": changes, "total_cost_cents": po.total_cost_cents})
        return po

    def reject(self, actor: User, po_id: str, reason: Any) -> PurchaseOrder:
        self.access.require(actor, P.PURCHASE_DECIDE)
        po = self._pending(po_id)
        reason = require_text(reason, "reason", 500)
        po.status = PurchaseOrderStatus.REJECTED
        po.decision = PurchaseDecision(decision=PurchaseDecisionType.REJECTED, by=actor.id,
                                       at=self.clock.now(), reason=reason)
        self.repos.purchase_orders.update(po)
        self.audit.record(actor, "PURCHASE_ORDER_REJECTED", "PurchaseOrder", po.id, {"reason": reason})
        return po

    def mark_received(self, actor: User, po_id: str) -> PurchaseOrder:
        """Close an approved PO once its goods were scanned in."""
        self.access.require_any(actor, P.INVENTORY_RECEIVE, P.PURCHASE_DECIDE)
        po = self.repos.purchase_orders.get(po_id)
        if po.status != PurchaseOrderStatus.APPROVED:
            raise InvalidTransitionError(f"{po.id} is {po.status.value}; only approved orders can be received")
        po.status = PurchaseOrderStatus.RECEIVED
        po.received_at, po.received_by = self.clock.now(), actor.id
        self.repos.purchase_orders.update(po)
        self.audit.record(actor, "PURCHASE_ORDER_RECEIVED", "PurchaseOrder", po.id)
        return po

    def _pending(self, po_id: str) -> PurchaseOrder:
        po = self.repos.purchase_orders.get(po_id)
        if po.status != PurchaseOrderStatus.PENDING_APPROVAL:
            raise InvalidTransitionError(f"{po.id} was already decided ({po.status.value})")
        return po
