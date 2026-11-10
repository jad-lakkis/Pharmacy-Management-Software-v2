"""Payments and receipts (US8.1)."""

from datetime import date
from typing import Any, Optional

from ..domain.enums import MovementReason, PaymentMethod, Permission, PrescriptionStatus
from ..domain.models import Sale, SaleLine, User
from ..errors import ConflictError, InvalidTransitionError, NotFoundError, ValidationError
from .context import Service, new_id, newest_first
from .validation import require_enum, require_int

P = Permission
PHARMACY_NAME = "Community Pharmacy"


class SalesService(Service):
    def __init__(self, ctx, prescriptions, inventory):
        super().__init__(ctx)
        self._prescriptions = prescriptions
        self._inventory = inventory

    def payable_orders(self, actor: User) -> list[dict[str, Any]]:
        """Figma "See orders": dispensed prescriptions not yet paid for."""
        self.access.require(actor, P.SALES_RECORD)
        rows = []
        for rx in sorted(self.repos.prescriptions.with_status(PrescriptionStatus.DISPENSED),
                         key=lambda r: r.dispensed_at or r.created_at):
            if self.repos.sales.for_prescription(rx.id):
                continue
            product = self.repos.products.get(rx.product_id)
            patient = self.repos.patients.find(rx.patient_id)
            rows.append({
                "prescription_id": rx.id,
                "patient": patient.full_name if patient else rx.patient_id,
                "medication": product.name,
                "quantity": rx.quantity,
                "unit_price_cents": product.unit_price_cents,
                "total_cents": rx.quantity * product.unit_price_cents,
                "dispensed_at": rx.dispensed_at.isoformat() if rx.dispensed_at else None,
                "refill_request_id": rx.refill_request_id,
            })
        return rows

    def record_prescription_payment(self, actor: User, prescription_id: str, method: Any,
                                    amount_tendered_cents: Any = None) -> Sale:
        """Take payment for one dispensed prescription, issue a receipt and
        hand it over (DISPENSED -> PICKED_UP)."""
        self.access.require(actor, P.SALES_RECORD)
        rx = self.repos.prescriptions.get(prescription_id)
        if rx.status != PrescriptionStatus.DISPENSED:
            raise InvalidTransitionError(f"{rx.id} is {rx.status.value}; only dispensed prescriptions can be paid")
        if self.repos.sales.for_prescription(rx.id):
            raise ConflictError(f"{rx.id} has already been paid")
        product = self.repos.products.get(rx.product_id)
        line = SaleLine(product_id=product.id, description=f"{product.name} {product.strength} ({rx.id})",
                        quantity=rx.quantity, unit_price_cents=product.unit_price_cents, prescription_id=rx.id)
        sale = self._make_sale(actor, [line], method, amount_tendered_cents,
                               prescription_ids=[rx.id], patient_id=rx.patient_id)
        self._prescriptions.transition(actor, rx.id, PrescriptionStatus.PICKED_UP, f"Paid, receipt {sale.receipt_number}")
        return sale

    def record_otc_sale(self, actor: User, items: list[dict[str, Any]], method: Any,
                        amount_tendered_cents: Any = None) -> Sale:
        """Over-the-counter sale of products that need no prescription."""
        self.access.require(actor, P.SALES_RECORD)
        if not isinstance(items, list) or not items:
            raise ValidationError("add at least one item")
        lines, seen = [], set()
        for item in items:
            product = self.repos.products.get(str(item.get("product_id", "")))
            if product.requires_prescription:
                raise ValidationError(f"{product.name} requires a prescription")
            if product.id in seen:
                raise ValidationError(f"{product.name} is listed twice")
            seen.add(product.id)
            qty = require_int(item.get("quantity"), f"quantity of {product.name}", 1, 1000)
            available = self._inventory.on_hand(product.id)
            if available < qty:
                raise ConflictError(f"insufficient stock of {product.name}: {available} available, {qty} needed")
            lines.append(SaleLine(product_id=product.id, description=f"{product.name} {product.strength}",
                                  quantity=qty, unit_price_cents=product.unit_price_cents))
        sale = self._make_sale(actor, lines, method, amount_tendered_cents)
        for line in lines:
            self._inventory.allocate(actor, line.product_id, line.quantity, MovementReason.SALE, sale.receipt_number)
        return sale

    def receipt(self, actor: User, receipt_or_sale_id: str) -> dict[str, Any]:
        self.access.require_any(actor, P.SALES_RECORD, P.REPORTS_VIEW)
        sale = self.repos.sales.by_receipt(receipt_or_sale_id) or self.repos.sales.find(receipt_or_sale_id)
        if sale is None:
            raise NotFoundError(f"receipt {receipt_or_sale_id} not found")
        return self.render_receipt(sale)

    def list_sales(self, actor: User, start: Optional[date] = None, end: Optional[date] = None) -> list[Sale]:
        self.access.require_any(actor, P.SALES_RECORD, P.REPORTS_VIEW)
        sales = self.repos.sales.filter(
            lambda s: (start is None or s.created_at.date() >= start) and (end is None or s.created_at.date() <= end)
        )
        return newest_first(sales, lambda s: s.created_at)

    def render_receipt(self, sale: Sale) -> dict[str, Any]:
        cashier = self.repos.users.find(sale.cashier_id)
        patient = self.repos.patients.find(sale.patient_id) if sale.patient_id else None
        return {
            "pharmacy": PHARMACY_NAME,
            "receipt_number": sale.receipt_number,
            "sale_id": sale.id,
            "issued_at": sale.created_at.isoformat(),
            "cashier": cashier.display_name if cashier else sale.cashier_id,
            "patient": patient.full_name if patient else None,
            "prescription_ids": sale.prescription_ids,
            "lines": [
                {"description": l.description, "quantity": l.quantity,
                 "unit_price_cents": l.unit_price_cents, "total_cents": l.total_cents}
                for l in sale.lines
            ],
            "total_cents": sale.total_cents,
            "method": sale.method.value,
            "amount_tendered_cents": sale.amount_tendered_cents,
            "change_cents": sale.change_cents,
        }

    def _make_sale(self, actor: User, lines: list[SaleLine], method: Any, tendered: Any,
                   prescription_ids: Optional[list[str]] = None, patient_id: Optional[str] = None) -> Sale:
        method = require_enum(PaymentMethod, method, "payment method")
        total = sum(l.total_cents for l in lines)
        if method == PaymentMethod.CARD:
            amount = total if tendered in (None, "") else require_int(tendered, "amount", 0)
            if amount != total:
                raise ValidationError("card payments must be for the exact total")
        else:
            if tendered in (None, ""):
                raise ValidationError("enter the cash amount received")
            amount = require_int(tendered, "amount", 0)
            if amount < total:
                raise ValidationError(f"cash received ({amount}) is less than the total ({total})")
        now = self.clock.now()
        receipt_number = f"R-{now:%Y%m%d}-{self.repos.counters.next('receipt'):05d}"
        sale = Sale(
            id=new_id("SAL"), receipt_number=receipt_number, lines=lines, total_cents=total, method=method,
            amount_tendered_cents=amount, change_cents=amount - total, cashier_id=actor.id, created_at=now,
            prescription_ids=prescription_ids or [], patient_id=patient_id,
        )
        self.repos.sales.add(sale)
        self.audit.record(actor, "SALE_RECORDED", "Sale", sale.id, {
            "receipt_number": receipt_number, "total_cents": total, "method": method.value,
            "prescription_ids": sale.prescription_ids,
        })
        return sale
