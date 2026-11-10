"""Domain entities.

Entities are plain dataclasses with no persistence logic. Repositories
serialise them through ``pharmacy.repositories.codec``. Money is stored in
integer cents; timestamps are timezone-aware UTC datetimes.
"""

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional

from .enums import (
    AlertOutcome,
    AlertSeverity,
    AlertStatus,
    AlertType,
    AllergySeverity,
    ConsentAction,
    MovementReason,
    NotificationChannel,
    NotificationStatus,
    PaymentMethod,
    Permission,
    PrescriptionStatus,
    PurchaseDecisionType,
    PurchaseOrderStatus,
    RefillStatus,
    Role,
)

# --------------------------------------------------------------------------
# Users and access (US1.1, US1.2, USER-24)
# --------------------------------------------------------------------------


@dataclass
class User:
    """A login. Staff users have a role; patient users link to a Patient.

    ``patient_id`` is the ownership link (USER-24): a patient user may only
    see and act on records belonging to that patient.
    """

    id: str
    username: str
    display_name: str
    role: Role
    password_hash: str
    password_salt: str
    active: bool = True
    granted_permissions: list[Permission] = field(default_factory=list)
    revoked_permissions: list[Permission] = field(default_factory=list)
    patient_id: Optional[str] = None
    created_at: Optional[datetime] = None


# --------------------------------------------------------------------------
# Patients and prescribers (US2.1, US2.2)
# --------------------------------------------------------------------------


@dataclass
class Allergy:
    substance: str
    reaction: str
    severity: AllergySeverity
    recorded_at: datetime
    recorded_by: str


@dataclass
class Patient:
    id: str
    first_name: str
    last_name: str
    date_of_birth: date
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    allergies: list[Allergy] = field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


@dataclass
class Prescriber:
    id: str
    name: str
    license_number: str
    phone: Optional[str] = None
    specialty: Optional[str] = None
    clinic: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# --------------------------------------------------------------------------
# Products and inventory (US5.1, US5.2)
# --------------------------------------------------------------------------


@dataclass
class Product:
    id: str
    name: str
    gtin: str  # 14-digit GTIN, normalised
    ingredients: list[str]
    strength: str
    dosage_form: str
    unit_price_cents: int
    unit_cost_cents: int
    requires_prescription: bool = True
    reorder_point: int = 0
    safety_stock: int = 0
    lead_time_days: int = 7
    max_daily_quantity: Optional[int] = None


@dataclass
class InventoryLot:
    id: str
    product_id: str
    lot_number: str
    expiry_date: date
    quantity_received: int
    quantity_on_hand: int
    received_at: datetime
    received_by: str
    supplier: Optional[str] = None


@dataclass
class StockMovement:
    id: str
    product_id: str
    lot_id: str
    delta: int  # positive in, negative out
    reason: MovementReason
    reference: str
    at: datetime
    by: str


# --------------------------------------------------------------------------
# Prescriptions (US3.1, US3.2)
# --------------------------------------------------------------------------


@dataclass
class StatusChange:
    from_status: Optional[PrescriptionStatus]
    to_status: PrescriptionStatus
    by: str
    at: datetime
    note: Optional[str] = None


@dataclass
class LotAllocation:
    lot_id: str
    lot_number: str
    expiry_date: date
    quantity: int


@dataclass
class Prescription:
    id: str  # human readable, e.g. RX-1042
    patient_id: str
    prescriber_id: str
    product_id: str
    quantity: int
    daily_quantity: int
    directions: str
    refills_authorized: int
    written_date: date
    status: PrescriptionStatus
    created_by: str
    created_at: datetime
    refills_used: int = 0
    status_history: list[StatusChange] = field(default_factory=list)
    held_from: Optional[PrescriptionStatus] = None
    verified_by: Optional[str] = None
    verified_at: Optional[datetime] = None
    dispensed_by: Optional[str] = None
    dispensed_at: Optional[datetime] = None
    allocations: list[LotAllocation] = field(default_factory=list)
    original_prescription_id: Optional[str] = None  # set on refill fills
    refill_request_id: Optional[str] = None

    VALIDITY_DAYS = 365

    @property
    def refills_remaining(self) -> int:
        return max(0, self.refills_authorized - self.refills_used)

    @property
    def days_supply(self) -> int:
        return max(1, math.ceil(self.quantity / max(1, self.daily_quantity)))

    @property
    def chain_root_id(self) -> str:
        """The original prescription this fill belongs to."""
        return self.original_prescription_id or self.id

    def expires_on(self) -> date:
        return self.written_date + timedelta(days=self.VALIDITY_DAYS)


# --------------------------------------------------------------------------
# Clinical alerts (US4.1, US4.2)
# --------------------------------------------------------------------------


@dataclass
class InteractionRule:
    id: str
    ingredient_a: str
    ingredient_b: str
    severity: AlertSeverity
    description: str
    reference_source: str


@dataclass
class AlertResolution:
    outcome: AlertOutcome
    rationale: str
    resolved_by: str
    resolved_at: datetime


@dataclass
class ClinicalAlert:
    id: str
    prescription_id: str
    patient_id: str
    alert_type: AlertType
    severity: AlertSeverity
    message: str
    triggering_data: dict[str, str]
    reference_basis: str
    created_at: datetime
    status: AlertStatus = AlertStatus.OPEN
    resolution: Optional[AlertResolution] = None


# --------------------------------------------------------------------------
# Purchasing (US6.1, US6.2)
# --------------------------------------------------------------------------


@dataclass
class PurchaseOrderLine:
    product_id: str
    product_name: str
    suggested_quantity: int
    quantity: int
    unit_cost_cents: int
    basis: dict[str, Any] = field(default_factory=dict)


@dataclass
class PurchaseDecision:
    decision: PurchaseDecisionType
    by: str
    at: datetime
    reason: Optional[str] = None
    changes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PurchaseOrder:
    id: str
    supplier: str
    lines: list[PurchaseOrderLine]
    status: PurchaseOrderStatus
    created_by: str
    created_at: datetime
    decision: Optional[PurchaseDecision] = None
    received_at: Optional[datetime] = None
    received_by: Optional[str] = None

    @property
    def total_cost_cents(self) -> int:
        return sum(line.quantity * line.unit_cost_cents for line in self.lines)


# --------------------------------------------------------------------------
# Refills and tracking (US7.1, USER-20, USER-21, USER-23)
# --------------------------------------------------------------------------


@dataclass
class RefillStatusChange:
    status: RefillStatus
    at: datetime
    by: str
    note: Optional[str] = None


@dataclass
class RefillRequest:
    id: str  # RF-001
    patient_id: str
    prescription_id: str  # the original prescription
    pickup_branch: str
    status: RefillStatus
    created_at: datetime
    history: list[RefillStatusChange] = field(default_factory=list)
    patient_note: Optional[str] = None
    fill_prescription_id: Optional[str] = None
    reviewed_by: Optional[str] = None
    rejection_reason: Optional[str] = None


# --------------------------------------------------------------------------
# Notifications and consent (US7.2)
# --------------------------------------------------------------------------


@dataclass
class ConsentEvent:
    action: ConsentAction
    at: datetime
    channel: Optional[NotificationChannel] = None
    policy_version: Optional[str] = None


@dataclass
class NotificationPreference:
    id: str  # equals the patient id: one preference record per patient
    patient_id: str
    channel: Optional[NotificationChannel] = None
    consent_given: bool = False
    policy_version: Optional[str] = None
    history: list[ConsentEvent] = field(default_factory=list)


@dataclass
class Notification:
    id: str
    patient_id: str
    subject: str
    message: str
    status: NotificationStatus
    created_at: datetime
    channel: Optional[NotificationChannel] = None
    destination: Optional[str] = None
    suppressed_reason: Optional[str] = None


# --------------------------------------------------------------------------
# Sales (US8.1)
# --------------------------------------------------------------------------


@dataclass
class SaleLine:
    product_id: str
    description: str
    quantity: int
    unit_price_cents: int
    prescription_id: Optional[str] = None

    @property
    def total_cents(self) -> int:
        return self.quantity * self.unit_price_cents


@dataclass
class Sale:
    id: str
    receipt_number: str
    lines: list[SaleLine]
    total_cents: int
    method: PaymentMethod
    amount_tendered_cents: int
    change_cents: int
    cashier_id: str
    created_at: datetime
    prescription_ids: list[str] = field(default_factory=list)
    patient_id: Optional[str] = None


# --------------------------------------------------------------------------
# Audit (US8.2)
# --------------------------------------------------------------------------


@dataclass
class AuditEntry:
    """One immutable audit record.

    Entries form a hash chain: ``entry_hash`` covers this entry's content
    and ``prev_hash``, so editing or removing any stored entry is detectable.
    """

    id: str
    sequence: int
    at: datetime
    actor_id: Optional[str]
    actor_name: str
    action: str
    entity_type: str
    entity_id: Optional[str]
    details: dict[str, Any]
    prev_hash: str
    entry_hash: str = ""


@dataclass
class Counter:
    """Named sequence used for human-readable ids (RX-1001, RF-001, ...)."""

    id: str
    value: int
