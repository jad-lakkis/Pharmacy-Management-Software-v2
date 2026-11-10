"""Entity-specific repositories with the queries the services need."""

from datetime import date, datetime
from typing import Iterable, Optional

from ..domain.enums import (
    OPEN_REFILL_STATUSES,
    AlertStatus,
    PrescriptionStatus,
    PurchaseOrderStatus,
)
from ..domain.models import (
    AuditEntry,
    ClinicalAlert,
    Counter,
    InteractionRule,
    InventoryLot,
    Notification,
    NotificationPreference,
    Patient,
    Prescriber,
    Prescription,
    Product,
    PurchaseOrder,
    RefillRequest,
    Sale,
    StockMovement,
    User,
)
from .base import AppendOnlyRepository, Repository
from .stores import Store


class UserRepository(Repository[User]):
    def __init__(self, store: Store):
        super().__init__(store, User)

    def by_username(self, username: str) -> Optional[User]:
        wanted = username.strip().lower()
        return self.first(lambda u: u.username.lower() == wanted)

    def by_patient(self, patient_id: str) -> Optional[User]:
        return self.first(lambda u: u.patient_id == patient_id)


class PatientRepository(Repository[Patient]):
    def __init__(self, store: Store):
        super().__init__(store, Patient)

    def search(self, query: str) -> list[Patient]:
        q = query.strip().lower()
        if not q:
            return self.list()
        return self.filter(
            lambda p: q in p.full_name.lower()
            or q in (p.phone or "").lower()
            or q in (p.email or "").lower()
            or q == p.id.lower()
        )

    def by_name_and_dob(self, first: str, last: str, dob: date) -> Optional[Patient]:
        f, l = first.strip().lower(), last.strip().lower()
        return self.first(
            lambda p: p.first_name.lower() == f and p.last_name.lower() == l and p.date_of_birth == dob
        )


class PrescriberRepository(Repository[Prescriber]):
    def __init__(self, store: Store):
        super().__init__(store, Prescriber)

    def by_license(self, license_number: str) -> Optional[Prescriber]:
        wanted = license_number.strip().upper()
        return self.first(lambda p: p.license_number.upper() == wanted)


class ProductRepository(Repository[Product]):
    def __init__(self, store: Store):
        super().__init__(store, Product)

    def by_gtin(self, gtin: str) -> Optional[Product]:
        return self.first(lambda p: p.gtin == gtin)


class LotRepository(Repository[InventoryLot]):
    def __init__(self, store: Store):
        super().__init__(store, InventoryLot)

    def for_product(self, product_id: str) -> list[InventoryLot]:
        return self.filter(lambda l: l.product_id == product_id)

    def by_product_and_lot(self, product_id: str, lot_number: str) -> Optional[InventoryLot]:
        wanted = lot_number.strip().upper()
        return self.first(lambda l: l.product_id == product_id and l.lot_number.upper() == wanted)


class MovementRepository(AppendOnlyRepository[StockMovement]):
    def __init__(self, store: Store):
        super().__init__(store, StockMovement)

    def for_product_since(self, product_id: str, since: datetime) -> list[StockMovement]:
        return self.filter(lambda m: m.product_id == product_id and m.at >= since)


class PrescriptionRepository(Repository[Prescription]):
    def __init__(self, store: Store):
        super().__init__(store, Prescription)

    def for_patient(self, patient_id: str) -> list[Prescription]:
        return self.filter(lambda r: r.patient_id == patient_id)

    def with_status(self, *statuses: PrescriptionStatus) -> list[Prescription]:
        wanted = set(statuses)
        return self.filter(lambda r: r.status in wanted)


class InteractionRuleRepository(Repository[InteractionRule]):
    def __init__(self, store: Store):
        super().__init__(store, InteractionRule)

    def between(self, ingredients_a: Iterable[str], ingredients_b: Iterable[str]) -> list[InteractionRule]:
        a = {i.lower() for i in ingredients_a}
        b = {i.lower() for i in ingredients_b}
        return self.filter(
            lambda r: (r.ingredient_a.lower() in a and r.ingredient_b.lower() in b)
            or (r.ingredient_b.lower() in a and r.ingredient_a.lower() in b)
        )


class AlertRepository(Repository[ClinicalAlert]):
    def __init__(self, store: Store):
        super().__init__(store, ClinicalAlert)

    def for_prescription(self, prescription_id: str) -> list[ClinicalAlert]:
        return self.filter(lambda a: a.prescription_id == prescription_id)

    def open_for_prescription(self, prescription_id: str) -> list[ClinicalAlert]:
        return self.filter(
            lambda a: a.prescription_id == prescription_id and a.status == AlertStatus.OPEN
        )


class PurchaseOrderRepository(Repository[PurchaseOrder]):
    def __init__(self, store: Store):
        super().__init__(store, PurchaseOrder)

    def with_status(self, *statuses: PurchaseOrderStatus) -> list[PurchaseOrder]:
        wanted = set(statuses)
        return self.filter(lambda po: po.status in wanted)


class RefillRepository(Repository[RefillRequest]):
    def __init__(self, store: Store):
        super().__init__(store, RefillRequest)

    def for_patient(self, patient_id: str) -> list[RefillRequest]:
        return self.filter(lambda r: r.patient_id == patient_id)

    def open_for_prescription(self, prescription_id: str) -> Optional[RefillRequest]:
        return self.first(
            lambda r: r.prescription_id == prescription_id and r.status in OPEN_REFILL_STATUSES
        )


class NotificationPreferenceRepository(Repository[NotificationPreference]):
    def __init__(self, store: Store):
        super().__init__(store, NotificationPreference)


class NotificationRepository(AppendOnlyRepository[Notification]):
    def __init__(self, store: Store):
        super().__init__(store, Notification)

    def for_patient(self, patient_id: str) -> list[Notification]:
        return self.filter(lambda n: n.patient_id == patient_id)


class SaleRepository(Repository[Sale]):
    def __init__(self, store: Store):
        super().__init__(store, Sale)

    def for_prescription(self, prescription_id: str) -> Optional[Sale]:
        return self.first(lambda s: prescription_id in s.prescription_ids)

    def by_receipt(self, receipt_number: str) -> Optional[Sale]:
        return self.first(lambda s: s.receipt_number == receipt_number)


class AuditRepository(AppendOnlyRepository[AuditEntry]):
    def __init__(self, store: Store):
        super().__init__(store, AuditEntry)


class CounterRepository(Repository[Counter]):
    def __init__(self, store: Store):
        super().__init__(store, Counter)

    def next(self, name: str, start: int = 1) -> int:
        counter = self.find(name)
        if counter is None:
            counter = Counter(id=name, value=start)
        else:
            counter.value += 1
        self.save(counter)
        return counter.value
