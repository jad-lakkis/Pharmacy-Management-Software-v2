"""Owner reports (US8.2). The audit log itself lives in ``audit.py``."""

from collections import Counter, defaultdict
from datetime import date
from typing import Any, Optional

from ..domain.enums import (
    AlertOutcome,
    AlertStatus,
    Permission,
    PrescriptionStatus,
    PurchaseOrderStatus,
    RefillStatus,
)
from ..domain.models import User
from ..errors import ValidationError
from .context import Service

P = Permission


class ReportService(Service):
    def __init__(self, ctx, inventory):
        super().__init__(ctx)
        self._inventory = inventory

    def _range(self, start: Optional[date], end: Optional[date]) -> tuple[date, date]:
        end = end or self.clock.today()
        start = start or end.replace(day=1)
        if start > end:
            raise ValidationError("start date must be on or before end date")
        return start, end

    def sales_report(self, actor: User, start: Optional[date] = None, end: Optional[date] = None) -> dict[str, Any]:
        self.access.require(actor, P.REPORTS_VIEW)
        start, end = self._range(start, end)
        sales = [s for s in self.repos.sales.list() if start <= s.created_at.date() <= end]
        by_method: dict[str, int] = defaultdict(int)
        by_day: dict[str, int] = defaultdict(int)
        products: Counter = Counter()
        for sale in sales:
            by_method[sale.method.value] += sale.total_cents
            by_day[sale.created_at.date().isoformat()] += sale.total_cents
            for line in sale.lines:
                products[line.description.split(" (")[0]] += line.quantity
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "transactions": len(sales),
            "total_cents": sum(s.total_cents for s in sales),
            "prescription_sales": sum(1 for s in sales if s.prescription_ids),
            "otc_sales": sum(1 for s in sales if not s.prescription_ids),
            "by_method_cents": dict(by_method),
            "by_day_cents": dict(sorted(by_day.items())),
            "top_products": [{"product": name, "units": units} for name, units in products.most_common(5)],
        }

    def operations_summary(self, actor: User) -> dict[str, Any]:
        self.access.require(actor, P.REPORTS_VIEW)
        prescriptions = self.repos.prescriptions.list()
        status_counts = Counter(rx.status.value for rx in prescriptions)
        turnaround = [
            (rx.dispensed_at - rx.created_at).total_seconds() / 3600
            for rx in prescriptions if rx.dispensed_at
        ]
        alerts = self.repos.alerts.list()
        today = self.clock.today()
        low, near = 0, 0
        for product in self.repos.products.list():
            if self._inventory.on_hand(product.id, today) <= product.reorder_point:
                low += 1
            near += sum(1 for l in self.repos.lots.for_product(product.id)
                        if l.quantity_on_hand > 0 and 0 < (l.expiry_date - today).days <= 90)
        return {
            "prescriptions_by_status": {s.value: status_counts.get(s.value, 0) for s in PrescriptionStatus},
            "average_hours_to_dispense": round(sum(turnaround) / len(turnaround), 1) if turnaround else None,
            "open_clinical_alerts": sum(1 for a in alerts if a.status == AlertStatus.OPEN),
            "alerts_overridden": sum(1 for a in alerts if a.resolution and a.resolution.outcome == AlertOutcome.OVERRIDE_CONTINUE),
            "products_at_or_below_reorder_point": low,
            "lots_expiring_within_90_days": near,
            "purchase_orders_pending": self.repos.purchase_orders.count(lambda po: po.status == PurchaseOrderStatus.PENDING_APPROVAL),
            "refills_open": self.repos.refills.count(lambda r: r.status in (RefillStatus.SUBMITTED, RefillStatus.UNDER_REVIEW)),
            "audit_entries": self.repos.audit.count(),
        }

    def alert_override_report(self, actor: User, start: Optional[date] = None, end: Optional[date] = None) -> list[dict[str, Any]]:
        self.access.require(actor, P.REPORTS_VIEW)
        start, end = self._range(start, end)
        rows = []
        for alert in self.repos.alerts.list():
            res = alert.resolution
            if not res or res.outcome != AlertOutcome.OVERRIDE_CONTINUE:
                continue
            if not start <= res.resolved_at.date() <= end:
                continue
            pharmacist = self.repos.users.find(res.resolved_by)
            rows.append({
                "alert_id": alert.id, "prescription_id": alert.prescription_id, "type": alert.alert_type.value,
                "severity": alert.severity.value, "message": alert.message, "rationale": res.rationale,
                "resolved_by": pharmacist.display_name if pharmacist else res.resolved_by,
                "resolved_at": res.resolved_at.isoformat(),
            })
        return sorted(rows, key=lambda r: r["resolved_at"], reverse=True)
