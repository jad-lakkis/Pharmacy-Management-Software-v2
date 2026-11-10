"""Enumerations shared by the domain, services and API."""

from enum import Enum


class Role(str, Enum):
    OWNER = "OWNER"
    MANAGER = "MANAGER"
    PHARMACIST = "PHARMACIST"
    TECHNICIAN = "TECHNICIAN"
    CASHIER = "CASHIER"
    PATIENT = "PATIENT"


class Permission(str, Enum):
    # Access control (US1.1)
    MANAGE_USERS = "access.manage"
    # Patients and prescribers (US2.1, US2.2)
    PATIENT_READ = "patients.read"
    PATIENT_WRITE = "patients.write"
    CLINICAL_READ = "patients.clinical_read"
    # Prescription workflow (US3.1, US3.2)
    RX_VIEW = "rx.view"
    RX_ENTER = "rx.enter"
    RX_VERIFY = "rx.verify"
    RX_DISPENSE = "rx.dispense"
    # Clinical alerts (US4.1, US4.2)
    ALERT_RESOLVE = "alerts.resolve"
    # Inventory (US5.1, US5.2)
    INVENTORY_VIEW = "inventory.view"
    INVENTORY_RECEIVE = "inventory.receive"
    INVENTORY_ALERTS = "inventory.alerts"
    PRODUCT_MANAGE = "inventory.products"
    # Purchasing (US6.1, US6.2)
    PURCHASE_VIEW = "purchasing.view"
    PURCHASE_DECIDE = "purchasing.decide"
    # Sales (US8.1)
    SALES_RECORD = "sales.record"
    # Reports and audit (US8.2)
    REPORTS_VIEW = "reports.view"
    AUDIT_VIEW = "audit.view"
    # Patient self-service (US7.1, US7.2)
    REFILL_REQUEST = "refills.request"
    REFILL_REVIEW = "refills.review"
    NOTIFY_PREFS = "notifications.self"


# Permissions that only make sense for a user linked to a patient record.
PATIENT_ONLY_PERMISSIONS = frozenset({Permission.REFILL_REQUEST, Permission.NOTIFY_PREFS})


class AllergySeverity(str, Enum):
    MILD = "MILD"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"


class PrescriptionStatus(str, Enum):
    RECEIVED = "RECEIVED"
    DATA_ENTERED = "DATA_ENTERED"
    AWAITING_VERIFICATION = "AWAITING_VERIFICATION"
    VERIFIED = "VERIFIED"
    DISPENSED = "DISPENSED"
    PICKED_UP = "PICKED_UP"
    ON_HOLD = "ON_HOLD"
    CANCELLED = "CANCELLED"


# Stages before stock leaves the shelf: holdable and cancellable.
PRE_DISPENSE_STATUSES = frozenset(
    {
        PrescriptionStatus.RECEIVED,
        PrescriptionStatus.DATA_ENTERED,
        PrescriptionStatus.AWAITING_VERIFICATION,
        PrescriptionStatus.VERIFIED,
    }
)


class AlertType(str, Enum):
    ALLERGY = "ALLERGY"
    INTERACTION = "INTERACTION"
    DUPLICATE_THERAPY = "DUPLICATE_THERAPY"
    DOSE_LIMIT = "DOSE_LIMIT"


class AlertSeverity(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"


class AlertStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    SUPERSEDED = "SUPERSEDED"


class AlertOutcome(str, Enum):
    OVERRIDE_CONTINUE = "OVERRIDE_CONTINUE"
    PRESCRIBER_CONFIRMED = "PRESCRIBER_CONFIRMED"
    PRESCRIBER_CHANGED_THERAPY = "PRESCRIBER_CHANGED_THERAPY"
    DISCONTINUE = "DISCONTINUE"


# Outcomes after which the prescription as written must not be verified.
BLOCKING_ALERT_OUTCOMES = frozenset(
    {AlertOutcome.PRESCRIBER_CHANGED_THERAPY, AlertOutcome.DISCONTINUE}
)


class MovementReason(str, Enum):
    RECEIVE = "RECEIVE"
    DISPENSE = "DISPENSE"
    SALE = "SALE"


class PurchaseOrderStatus(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RECEIVED = "RECEIVED"


class PurchaseDecisionType(str, Enum):
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"


class RefillStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    PICKED_UP = "PICKED_UP"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


OPEN_REFILL_STATUSES = frozenset(
    {
        RefillStatus.SUBMITTED,
        RefillStatus.UNDER_REVIEW,
        RefillStatus.APPROVED,
        RefillStatus.READY_FOR_PICKUP,
    }
)


class NotificationChannel(str, Enum):
    SMS = "SMS"
    EMAIL = "EMAIL"
    IN_APP = "IN_APP"


class ConsentAction(str, Enum):
    GIVEN = "GIVEN"
    WITHDRAWN = "WITHDRAWN"
    CHANNEL_CHANGED = "CHANNEL_CHANGED"


class NotificationStatus(str, Enum):
    SENT = "SENT"
    SUPPRESSED = "SUPPRESSED"


class PaymentMethod(str, Enum):
    CASH = "CASH"
    CARD = "CARD"
