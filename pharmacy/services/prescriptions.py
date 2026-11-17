"""Prescription workflow (US3.1) with mandatory verification (US3.2)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable, Optional

from ..domain.enums import (
    BLOCKING_ALERT_OUTCOMES,
    PRE_DISPENSE_STATUSES,
    AlertStatus,
    MovementReason,
    Permission,
    PrescriptionStatus,
)
from ..domain.models import Prescription, StatusChange, User
from ..errors import InvalidTransitionError, ValidationError
from .context import Service, newest_first
from .validation import optional_text, require_date, require_enum, require_int, require_text

P = Permission
S = PrescriptionStatus

# (from, to) -> permission needed. Anything not listed is not a legal move.
TRANSITIONS: dict[tuple[PrescriptionStatus, PrescriptionStatus], Permission] = {
    (S.RECEIVED, S.DATA_ENTERED): P.RX_ENTER,
    (S.DATA_ENTERED, S.AWAITING_VERIFICATION): P.RX_ENTER,
    (S.AWAITING_VERIFICATION, S.VERIFIED): P.RX_VERIFY,
    (S.AWAITING_VERIFICATION, S.DATA_ENTERED): P.RX_VERIFY,  # returned for correction
    (S.VERIFIED, S.DISPENSED): P.RX_DISPENSE,
    (S.DISPENSED, S.PICKED_UP): P.SALES_RECORD,
}
for _status in PRE_DISPENSE_STATUSES:
    TRANSITIONS[(_status, S.ON_HOLD)] = P.RX_ENTER
    TRANSITIONS[(S.ON_HOLD, _status)] = P.RX_ENTER  # resume: only to held_from
    TRANSITIONS[(_status, S.CANCELLED)] = P.RX_ENTER
TRANSITIONS[(S.ON_HOLD, S.CANCELLED)] = P.RX_ENTER

NOTE_REQUIRED = {
    (S.AWAITING_VERIFICATION, S.DATA_ENTERED),
    *((s, S.CANCELLED) for s in PRE_DISPENSE_STATUSES | {S.ON_HOLD}),
    *((s, S.ON_HOLD) for s in PRE_DISPENSE_STATUSES),
}

EDITABLE_STATUSES = {S.RECEIVED, S.DATA_ENTERED}
EDITABLE_FIELDS = ("prescriber_id", "product_id", "quantity", "daily_quantity", "directions",
                   "refills_authorized", "written_date")
MAX_REFILLS = 11

StatusListener = Callable[[User, Prescription, PrescriptionStatus, PrescriptionStatus], None]


class PrescriptionService(Service):
    def __init__(self, ctx, alerts, inventory):
        super().__init__(ctx)
        self._alerts = alerts
        self._inventory = inventory
        self._listeners: list[StatusListener] = []

    def add_listener(self, listener: StatusListener) -> None:
        """Called after every status change (used by refill tracking)."""
        self._listeners.append(listener)

    # ---- creation and editing -------------------------------------------
    def create(
        self,
        actor: User,
        patient_id: str,
        prescriber_id: str,
        product_id: str,
        quantity: Any,
        daily_quantity: Any,
        directions: str,
        refills_authorized: Any = 0,
        written_date: Any = None,
    ) -> Prescription:
        self.access.require(actor, P.RX_ENTER)
        self.repos.patients.get(patient_id)
        fields = self._clean_fields({
            "prescriber_id": prescriber_id, "product_id": product_id, "quantity": quantity,
            "daily_quantity": daily_quantity, "directions": directions,
            "refills_authorized": refills_authorized,
            "written_date": written_date if written_date is not None else self.clock.today(),
        })
        now = self.clock.now()
        rx = Prescription(
            id=self._next_id(), patient_id=patient_id, status=S.RECEIVED, created_by=actor.id, created_at=now,
            status_history=[StatusChange(None, S.RECEIVED, actor.id, now, "Prescription received")],
            **fields,
        )
        self.repos.prescriptions.add(rx)
        self.audit.record(actor, "PRESCRIPTION_CREATED", "Prescription", rx.id,
                          {"patient_id": patient_id, "product_id": rx.product_id, "quantity": rx.quantity})
        return rx

    def create_refill_fill(self, actor: User, original: Prescription, refill_request_id: str) -> Prescription:
        """A new fill for an approved refill. It enters the workflow at
        DATA_ENTERED and must still be screened and verified."""
        self.access.require(actor, P.RX_ENTER)
        now = self.clock.now()
        rx = Prescription(
            id=self._next_id(), patient_id=original.patient_id, prescriber_id=original.prescriber_id,
            product_id=original.product_id, quantity=original.quantity, daily_quantity=original.daily_quantity,
            directions=original.directions, refills_authorized=0, written_date=original.written_date,
            status=S.DATA_ENTERED, created_by=actor.id, created_at=now,
            status_history=[StatusChange(None, S.DATA_ENTERED, actor.id, now, f"Refill fill for {refill_request_id}")],
            original_prescription_id=original.id, refill_request_id=refill_request_id,
        )
        self.repos.prescriptions.add(rx)
        self.audit.record(actor, "REFILL_FILL_CREATED", "Prescription", rx.id,
                          {"original_prescription_id": original.id, "refill_request_id": refill_request_id})
        return rx

    def update_details(self, actor: User, prescription_id: str, **changes: Any) -> Prescription:
        self.access.require(actor, P.RX_ENTER)
        unknown = set(changes) - set(EDITABLE_FIELDS)
        if unknown:
            raise ValidationError(f"cannot update: {', '.join(sorted(unknown))}")
        rx = self.repos.prescriptions.get(prescription_id)
        if rx.status not in EDITABLE_STATUSES:
            raise InvalidTransitionError(
                f"{rx.id} is {rx.status.value}; details can only change while received or in data entry"
            )
        if rx.original_prescription_id and ({"product_id", "refills_authorized"} & set(changes)):
            raise ValidationError("a refill fill must keep the original product and has no refills of its own")
        merged = {k: getattr(rx, k) for k in EDITABLE_FIELDS}
        merged.update(changes)
        fields = self._clean_fields(merged, allow_old_written_date=bool(rx.original_prescription_id))
        changed = {k: [str(getattr(rx, k)), str(v)] for k, v in fields.items() if getattr(rx, k) != v}
        for key, value in fields.items():
            setattr(rx, key, value)
        self.repos.prescriptions.update(rx)
        self.audit.record(actor, "PRESCRIPTION_UPDATED", "Prescription", rx.id, {"changed": changed})
        return rx

    # ---- reading ---------------------------------------------------------
    def get(self, actor: User, prescription_id: str) -> Prescription:
        self.access.require(actor, P.RX_VIEW)
        return self.repos.prescriptions.get(prescription_id)

    def list(self, actor: User, status: Optional[str] = None, patient_id: Optional[str] = None) -> list[Prescription]:
        self.access.require(actor, P.RX_VIEW)
        wanted = require_enum(S, status, "status") if status else None
        items = self.repos.prescriptions.filter(
            lambda r: (wanted is None or r.status == wanted) and (patient_id is None or r.patient_id == patient_id)
        )
        return newest_first(items, lambda r: r.created_at)

    def verification_queue(self, actor: User) -> list[dict[str, Any]]:
        self.access.require(actor, P.RX_VERIFY)
        queue = []
        for rx in sorted(self.repos.prescriptions.with_status(S.AWAITING_VERIFICATION), key=lambda r: r.created_at):
            current = [a for a in self.repos.alerts.for_prescription(rx.id) if a.status != AlertStatus.SUPERSEDED]
            queue.append({
                "prescription": rx,
                "alerts": current,
                "open_alerts": sum(1 for a in current if a.status == AlertStatus.OPEN),
                "can_verify": self.verification_blockers(rx) == [],
                "blockers": self.verification_blockers(rx),
            })
        return queue

    def allowed_transitions(self, actor: User, rx: Prescription) -> list[PrescriptionStatus]:
        """Targets this actor could move ``rx`` to right now (for the UI)."""
        result = []
        for (src, dst), perm in TRANSITIONS.items():
            if src != rx.status or not self.access.has(actor, perm):
                continue
            if src == S.ON_HOLD and dst != S.CANCELLED and dst != rx.held_from:
                continue
            result.append(dst)
        order = list(S)
        return sorted(set(result), key=order.index)

    def verification_blockers(self, rx: Prescription) -> list[str]:
        blockers = []
        for alert in self.repos.alerts.for_prescription(rx.id):
            if alert.status == AlertStatus.OPEN:
                blockers.append(f"alert {alert.id} ({alert.alert_type.value}) is not resolved")
            elif (alert.status == AlertStatus.RESOLVED and alert.resolution
                  and alert.resolution.outcome in BLOCKING_ALERT_OUTCOMES):
                blockers.append(
                    f"alert {alert.id} was resolved as {alert.resolution.outcome.value}; return or cancel the prescription"
                )
        return blockers

    # ---- workflow --------------------------------------------------------
    def transition(self, actor: User, prescription_id: str, to_status: Any, note: Optional[str] = None) -> Prescription:
        target = require_enum(S, to_status, "status")
        rx = self.repos.prescriptions.get(prescription_id)
        source = rx.status
        permission = TRANSITIONS.get((source, target))
        if permission is None:
            raise InvalidTransitionError(f"{rx.id} cannot move from {source.value} to {target.value}")
        self.access.require(actor, permission)
        note = optional_text(note, "note", 500)
        if (source, target) in NOTE_REQUIRED and not note:
            raise ValidationError("a note is required for this change")

        if source == S.ON_HOLD and target != S.CANCELLED:
            if target != rx.held_from:
                raise InvalidTransitionError(f"{rx.id} was held from {rx.held_from.value if rx.held_from else '?'}; "
                                             "it can only resume to that stage")
            rx.held_from = None
        if target == S.ON_HOLD:
            rx.held_from = source

        if target == S.AWAITING_VERIFICATION:
            self._check_ready_for_verification(rx)
        elif target == S.VERIFIED:
            blockers = self.verification_blockers(rx)
            if blockers:
                raise InvalidTransitionError("cannot verify: " + "; ".join(blockers))
            rx.verified_by, rx.verified_at = actor.id, self.clock.now()
        elif target == S.DATA_ENTERED and source == S.AWAITING_VERIFICATION:
            rx.verified_by = rx.verified_at = None
        elif target == S.DISPENSED:
            self._dispense(actor, rx)
        elif target == S.PICKED_UP:
            if self.repos.sales.for_prescription(rx.id) is None:
                raise InvalidTransitionError(f"{rx.id} has not been paid for; record the payment first")

        rx.status = target
        rx.status_history.append(StatusChange(source, target, actor.id, self.clock.now(), note))
        self.repos.prescriptions.update(rx)
        if target == S.AWAITING_VERIFICATION:
            self._alerts.screen(actor, rx)
        self.audit.record(actor, "PRESCRIPTION_STATUS_CHANGED", "Prescription", rx.id,
                          {"from": source.value, "to": target.value, "note": note})
        for listener in self._listeners:
            listener(actor, rx, source, target)
        return rx

    # ---- helpers ---------------------------------------------------------
    def _dispense(self, actor: User, rx: Prescription) -> None:
        if not rx.verified_by:
            raise InvalidTransitionError(f"{rx.id} has no pharmacist verification")
        verifier = self.repos.users.find(rx.verified_by)
        if verifier is None or not self.access.has(verifier, P.RX_VERIFY):
            raise InvalidTransitionError(f"{rx.id} was not verified by a user who may verify prescriptions")
        if self.clock.today() > rx.expires_on():
            raise InvalidTransitionError(f"{rx.id} expired on {rx.expires_on().isoformat()}")
        rx.allocations = self._inventory.allocate(actor, rx.product_id, rx.quantity, MovementReason.DISPENSE, rx.id)
        rx.dispensed_by, rx.dispensed_at = actor.id, self.clock.now()

    def _check_ready_for_verification(self, rx: Prescription) -> None:
        self._clean_fields({k: getattr(rx, k) for k in EDITABLE_FIELDS}, allow_old_written_date=bool(rx.original_prescription_id))
        self.repos.patients.get(rx.patient_id)

    def _clean_fields(self, raw: dict[str, Any], allow_old_written_date: bool = False) -> dict[str, Any]:
        prescriber = self.repos.prescribers.get(require_text(raw.get("prescriber_id"), "prescriber"))
        product = self.repos.products.get(require_text(raw.get("product_id"), "product"))
        written = require_date(raw.get("written_date"), "written date")
        today = self.clock.today()
        if written > today:
            raise ValidationError("written date cannot be in the future")
        if not allow_old_written_date and written < today - timedelta(days=Prescription.VALIDITY_DAYS):
            raise ValidationError("prescription is older than one year and no longer valid")
        quantity = require_int(raw.get("quantity"), "quantity", 1, 10_000)
        daily = require_int(raw.get("daily_quantity"), "daily quantity", 1, 1_000)
        if daily > quantity:
            raise ValidationError("daily quantity cannot exceed the quantity supplied")
        return {
            "prescriber_id": prescriber.id,
            "product_id": product.id,
            "quantity": quantity,
            "daily_quantity": daily,
            "directions": require_text(raw.get("directions"), "directions", 300),
            "refills_authorized": require_int(raw.get("refills_authorized", 0), "refills", 0, MAX_REFILLS),
            "written_date": written,
        }

    def _next_id(self) -> str:
        return f"RX-{self.repos.counters.next('prescription', start=1001)}"
