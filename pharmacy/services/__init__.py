"""Service layer: one service per feature area, wired by ``PharmacyApp``."""

from typing import Optional

from ..clock import Clock, SystemClock
from ..repositories import Repositories
from .access import AccessService
from .audit import AuditService
from .clinical_alerts import ClinicalAlertService
from .context import ServiceContext
from .inventory import InventoryService
from .notifications import NotificationService
from .patients import PatientService
from .prescriptions import PrescriptionService
from .purchasing import PurchasingService
from .refills import RefillService
from .reports import ReportService
from .sales import SalesService


class PharmacyApp:
    """Composition root. Tests typically do::

        app = PharmacyApp(Repositories.in_memory(), FixedClock(datetime(...)))
    """

    def __init__(self, repos: Repositories, clock: Optional[Clock] = None):
        self.repos = repos
        self.clock = clock or SystemClock()
        ctx = ServiceContext(repos=repos, clock=self.clock)
        self.audit = AuditService(ctx)
        ctx.audit = self.audit
        self.access = AccessService(ctx)
        ctx.access = self.access
        self.patients = PatientService(ctx)
        self.inventory = InventoryService(ctx)
        self.alerts = ClinicalAlertService(ctx)
        self.prescriptions = PrescriptionService(ctx, self.alerts, self.inventory)
        self.purchasing = PurchasingService(ctx, self.inventory)
        self.notifications = NotificationService(ctx)
        self.refills = RefillService(ctx, self.prescriptions, self.notifications)
        self.sales = SalesService(ctx, self.prescriptions, self.inventory)
        self.reports = ReportService(ctx, self.inventory)
        self.prescriptions.add_listener(self.refills.on_prescription_status_changed)

    def transaction(self):
        return self.repos.transaction()


__all__ = ["PharmacyApp"]
