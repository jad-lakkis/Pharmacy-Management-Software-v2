"""Repository layer.

``Repositories`` bundles one repository per aggregate plus a shared
transaction scope. Build it with ``Repositories.in_memory()`` for tests or
``Repositories.sqlite(path)`` for a persistent database.
"""

from pathlib import Path
from typing import Callable

from .base import AppendOnlyRepository, Repository
from .domain import (
    AlertRepository,
    AuditRepository,
    CounterRepository,
    InteractionRuleRepository,
    LotRepository,
    MovementRepository,
    NotificationPreferenceRepository,
    NotificationRepository,
    PatientRepository,
    PrescriberRepository,
    PrescriptionRepository,
    ProductRepository,
    PurchaseOrderRepository,
    RefillRepository,
    SaleRepository,
    SupplierRepository,
    UserRepository,
)
from .stores import MemoryStore, SqliteDatabase, SqliteStore, Store, TransactionManager

_TABLES = {
    "users": UserRepository,
    "patients": PatientRepository,
    "prescribers": PrescriberRepository,
    "products": ProductRepository,
    "lots": LotRepository,
    "movements": MovementRepository,
    "prescriptions": PrescriptionRepository,
    "interaction_rules": InteractionRuleRepository,
    "alerts": AlertRepository,
    "purchase_orders": PurchaseOrderRepository,
    "suppliers": SupplierRepository,
    "refills": RefillRepository,
    "notification_preferences": NotificationPreferenceRepository,
    "notifications": NotificationRepository,
    "sales": SaleRepository,
    "audit": AuditRepository,
    "counters": CounterRepository,
}


class Repositories:
    users: UserRepository
    patients: PatientRepository
    prescribers: PrescriberRepository
    products: ProductRepository
    lots: LotRepository
    movements: MovementRepository
    prescriptions: PrescriptionRepository
    interaction_rules: InteractionRuleRepository
    alerts: AlertRepository
    purchase_orders: PurchaseOrderRepository
    suppliers: SupplierRepository
    refills: RefillRepository
    notification_preferences: NotificationPreferenceRepository
    notifications: NotificationRepository
    sales: SaleRepository
    audit: AuditRepository
    counters: CounterRepository

    def __init__(self, store_factory: Callable[[str], Store], transaction: TransactionManager | None = None):
        stores = {name: store_factory(name) for name in _TABLES}
        for name, repo_cls in _TABLES.items():
            setattr(self, name, repo_cls(stores[name]))
        self._db = None
        self._transaction = transaction or TransactionManager(
            [s for s in stores.values() if isinstance(s, MemoryStore)], None
        )

    def transaction(self) -> TransactionManager:
        """``with repos.transaction(): ...`` – all-or-nothing writes."""
        return self._transaction

    def is_empty(self) -> bool:
        return self.users.count() == 0

    def close(self) -> None:
        if self._db is not None:
            self._db.close()

    @classmethod
    def in_memory(cls) -> "Repositories":
        return cls(lambda name: MemoryStore())

    @classmethod
    def sqlite(cls, path: str | Path) -> "Repositories":
        db = SqliteDatabase(path)
        repos = cls(lambda name: SqliteStore(db, name), TransactionManager([], db))
        repos._db = db
        return repos


__all__ = [
    "Repositories",
    "Repository",
    "AppendOnlyRepository",
    "MemoryStore",
    "SqliteStore",
    "SqliteDatabase",
]
