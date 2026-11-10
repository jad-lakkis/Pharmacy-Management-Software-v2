"""Refill requests and order tracking (US7.1: USER-13, USER-20, USER-21, USER-23).

Ownership (USER-24): a patient account only ever sees its own patient's
records. Requests for anything else answer "not found", so ids belonging
to other patients cannot be probed.
"""

from typing import Any, Optional

from ..domain.enums import (
    OPEN_REFILL_STATUSES,
    Permission,
    PrescriptionStatus,
    RefillStatus,
    Role,
)
from ..domain.models import Prescription, RefillRequest, RefillStatusChange, User
from ..errors import InvalidTransitionError, NotFoundError, PermissionDeniedError, ValidationError
from .context import Service, newest_first
from .validation import optional_text, require_text

P = Permission
S = PrescriptionStatus
R = RefillStatus

BRANCHES = ("Main branch", "Hamra branch", "Achrafieh branch")

ORDER_STAGE_LABELS = {
    S.RECEIVED: "Received",
    S.DATA_ENTERED: "Being prepared",
    S.AWAITING_VERIFICATION: "Pharmacist review",
    S.VERIFIED: "Approved by pharmacist",
    S.DISPENSED: "Ready for pickup",
    S.PICKED_UP: "Picked up",
    S.ON_HOLD: "On hold - the pharmacy will contact you",
    S.CANCELLED: "Cancelled",
}

REFILL_STAGE_LABELS = {
    R.SUBMITTED: "Waiting for pharmacy review",
    R.UNDER_REVIEW: "Pharmacist is reviewing",
    R.APPROVED: "Approved - being prepared",
    R.READY_FOR_PICKUP: "Ready for pickup",
    R.PICKED_UP: "Picked up",
    R.REJECTED: "Not approved",
    R.CANCELLED: "Cancelled",
}


class RefillService(Service):
    def __init__(self, ctx, prescriptions, notifications):
        super().__init__(ctx)
        self._prescriptions = prescriptions
        self._notifications = notifications

    # ---- patient side (USER-21, USER-20) --------------------------------
    def refillable_prescriptions(self, actor: User) -> list[dict[str, Any]]:
        """Figma "choose a medication to refill"."""
        patient_id = self._own_patient(actor)
        rows = []
        for rx in self.repos.prescriptions.for_patient(patient_id):
            if rx.original_prescription_id is not None:
                continue
            problem = self._refill_problem(rx)
            product = self.repos.products.find(rx.product_id)
            rows.append({
                "prescription_id": rx.id,
                "medication": product.name if product else rx.product_id,
                "strength": product.strength if product else None,
                "refills_remaining": rx.refills_remaining,
                "expires_on": rx.expires_on().isoformat(),
                "can_request": problem is None,
                "reason": problem,
            })
        return rows

    def request_refill(self, actor: User, prescription_id: str, pickup_branch: str = BRANCHES[0],
                       note: Optional[str] = None) -> RefillRequest:
        patient_id = self._own_patient(actor)
        rx = self._own_prescription(patient_id, prescription_id)
        if rx.original_prescription_id is not None:
            rx = self.repos.prescriptions.get(rx.original_prescription_id)
        problem = self._refill_problem(rx)
        if problem:
            raise ValidationError(problem)
        branch = require_text(pickup_branch, "pickup branch", 60)
        if branch not in BRANCHES:
            raise ValidationError(f"pickup branch must be one of: {', '.join(BRANCHES)}")
        now = self.clock.now()
        refill = RefillRequest(
            id=f"RF-{self.repos.counters.next('refill'):03d}", patient_id=patient_id, prescription_id=rx.id,
            pickup_branch=branch, status=R.SUBMITTED, created_at=now,
            history=[RefillStatusChange(R.SUBMITTED, now, actor.id, "Request submitted")],
            patient_note=optional_text(note, "note", 300),
        )
        self.repos.refills.add(refill)
        self.audit.record(actor, "REFILL_REQUESTED", "RefillRequest", refill.id,
                          {"prescription_id": rx.id, "pickup_branch": branch})
        self._notify(refill, "Refill request received",
                     f"We received refill request {refill.id}. It is waiting for pharmacy review.")
        return refill

    def cancel_refill(self, actor: User, refill_id: str) -> RefillRequest:
        patient_id = self._own_patient(actor)
        refill = self._own_refill(patient_id, refill_id)
        if refill.status != R.SUBMITTED:
            raise InvalidTransitionError("a request can only be cancelled before the pharmacy starts reviewing it")
        self._set_status(actor, refill, R.CANCELLED, "Cancelled by patient")
        return refill

    def my_refills(self, actor: User) -> list[dict[str, Any]]:
        patient_id = self._own_patient(actor)
        refills = newest_first(self.repos.refills.for_patient(patient_id), lambda r: r.created_at)
        return [self._refill_view(r) for r in refills]

    def get_refill(self, actor: User, refill_id: str) -> dict[str, Any]:
        if actor.role == Role.PATIENT:
            refill = self._own_refill(self._own_patient(actor), refill_id)
        else:
            self.access.require(actor, P.REFILL_REVIEW)
            refill = self.repos.refills.get(refill_id)
        return self._refill_view(refill)

    def track_orders(self, actor: User) -> list[dict[str, Any]]:
        """USER-20: every prescription order for this patient with its
        timestamped stage timeline, newest first."""
        patient_id = self._own_patient(actor)
        orders = []
        for rx in newest_first(self.repos.prescriptions.for_patient(patient_id), lambda r: r.created_at):
            product = self.repos.products.find(rx.product_id)
            orders.append({
                "prescription_id": rx.id,
                "medication": product.name if product else rx.product_id,
                "status": rx.status.value,
                "stage": ORDER_STAGE_LABELS[rx.status],
                "refill_request_id": rx.refill_request_id,
                "timeline": [
                    {"stage": ORDER_STAGE_LABELS[c.to_status], "status": c.to_status.value, "at": c.at.isoformat()}
                    for c in rx.status_history
                ],
            })
        return orders

    # ---- pharmacy side ---------------------------------------------------
    def review_queue(self, actor: User) -> list[dict[str, Any]]:
        self.access.require(actor, P.REFILL_REVIEW)
        pending = self.repos.refills.filter(lambda r: r.status in (R.SUBMITTED, R.UNDER_REVIEW))
        return [self._refill_view(r, staff=True) for r in sorted(pending, key=lambda r: r.created_at)]

    def start_review(self, actor: User, refill_id: str) -> RefillRequest:
        self.access.require(actor, P.REFILL_REVIEW)
        refill = self.repos.refills.get(refill_id)
        if refill.status != R.SUBMITTED:
            raise InvalidTransitionError(f"{refill.id} is {refill.status.value}, not submitted")
        refill.reviewed_by = actor.id
        self._set_status(actor, refill, R.UNDER_REVIEW, "Pharmacist started review")
        return refill

    def approve(self, actor: User, refill_id: str) -> RefillRequest:
        self.access.require(actor, P.REFILL_REVIEW)
        refill = self.repos.refills.get(refill_id)
        if refill.status not in (R.SUBMITTED, R.UNDER_REVIEW):
            raise InvalidTransitionError(f"{refill.id} is {refill.status.value} and cannot be approved")
        original = self.repos.prescriptions.get(refill.prescription_id)
        if original.refills_remaining <= 0:
            raise ValidationError("no refills remain on this prescription")
        if self.clock.today() > original.expires_on():
            raise ValidationError("the prescription has expired; a new prescription is needed")
        fill = self._prescriptions.create_refill_fill(actor, original, refill.id)
        original = self.repos.prescriptions.get(original.id)
        original.refills_used += 1
        self.repos.prescriptions.update(original)
        refill.fill_prescription_id = fill.id
        refill.reviewed_by = actor.id
        self._set_status(actor, refill, R.APPROVED, f"Approved; being prepared as {fill.id}")
        self._notify(refill, "Refill approved", f"Refill {refill.id} was approved and is being prepared.")
        return refill

    def reject(self, actor: User, refill_id: str, reason: Any) -> RefillRequest:
        self.access.require(actor, P.REFILL_REVIEW)
        refill = self.repos.refills.get(refill_id)
        if refill.status not in (R.SUBMITTED, R.UNDER_REVIEW):
            raise InvalidTransitionError(f"{refill.id} is {refill.status.value} and cannot be rejected")
        refill.rejection_reason = require_text(reason, "reason", 300)
        refill.reviewed_by = actor.id
        self._set_status(actor, refill, R.REJECTED, refill.rejection_reason)
        self._notify(refill, "Refill not approved",
                     f"Refill {refill.id} was not approved: {refill.rejection_reason}")
        return refill

    # ---- tracking system (USER-23): follow the fill's workflow ----------
    def on_prescription_status_changed(self, actor: User, rx: Prescription,
                                       old: PrescriptionStatus, new: PrescriptionStatus) -> None:
        if not rx.refill_request_id:
            return
        refill = self.repos.refills.find(rx.refill_request_id)
        if refill is None or refill.status not in OPEN_REFILL_STATUSES:
            return
        if new == S.DISPENSED:
            self._set_status(actor, refill, R.READY_FOR_PICKUP, f"{rx.id} dispensed")
            self._notify(refill, "Ready for pickup",
                         f"Refill {refill.id} is ready for pickup at {refill.pickup_branch}.")
        elif new == S.PICKED_UP:
            self._set_status(actor, refill, R.PICKED_UP, f"{rx.id} picked up")
        elif new == S.CANCELLED:
            original = self.repos.prescriptions.get(refill.prescription_id)
            original.refills_used = max(0, original.refills_used - 1)
            self.repos.prescriptions.update(original)
            self._set_status(actor, refill, R.CANCELLED, f"Fill {rx.id} cancelled by the pharmacy; refill returned")
            self._notify(refill, "Refill cancelled",
                         f"Refill {refill.id} was cancelled by the pharmacy. Please contact us for details.")

    # ---- helpers ---------------------------------------------------------
    def _refill_problem(self, rx: Prescription) -> Optional[str]:
        if rx.status == S.CANCELLED:
            return "this prescription was cancelled"
        if rx.status not in (S.DISPENSED, S.PICKED_UP):
            return "this prescription has not been filled yet"
        if rx.refills_remaining <= 0:
            return "no refills remain; ask your prescriber for a new prescription"
        if self.clock.today() > rx.expires_on():
            return "this prescription has expired; ask your prescriber for a new one"
        if self.repos.refills.open_for_prescription(rx.id):
            return "a refill request for this prescription is already in progress"
        return None

    def _set_status(self, actor: User, refill: RefillRequest, status: RefillStatus, note: str) -> None:
        old = refill.status
        refill.status = status
        refill.history.append(RefillStatusChange(status, self.clock.now(), actor.id, note))
        self.repos.refills.update(refill)
        self.audit.record(actor, "REFILL_STATUS_CHANGED", "RefillRequest", refill.id,
                          {"from": old.value, "to": status.value, "note": note})

    def _notify(self, refill: RefillRequest, subject: str, message: str) -> None:
        self._notifications.notify(refill.patient_id, subject, message)

    def _refill_view(self, refill: RefillRequest, staff: bool = False) -> dict[str, Any]:
        rx = self.repos.prescriptions.find(refill.prescription_id)
        product = self.repos.products.find(rx.product_id) if rx else None
        view = {
            "id": refill.id,
            "prescription_id": refill.prescription_id,
            "medication": product.name if product else None,
            "pickup_branch": refill.pickup_branch,
            "status": refill.status.value,
            "stage": REFILL_STAGE_LABELS[refill.status],
            "created_at": refill.created_at.isoformat(),
            "fill_prescription_id": refill.fill_prescription_id,
            "rejection_reason": refill.rejection_reason,
            "patient_note": refill.patient_note,
            "can_cancel": refill.status == R.SUBMITTED,
            "timeline": [
                {"status": h.status.value, "stage": REFILL_STAGE_LABELS[h.status], "at": h.at.isoformat(), "note": h.note}
                for h in refill.history
            ],
        }
        if staff:
            patient = self.repos.patients.find(refill.patient_id)
            view["patient"] = patient.full_name if patient else refill.patient_id
            view["patient_id"] = refill.patient_id
            view["refills_remaining"] = rx.refills_remaining if rx else None
        return view

    def _own_patient(self, actor: User) -> str:
        self.access.require(actor, P.REFILL_REQUEST)
        if actor.role != Role.PATIENT or not actor.patient_id:
            raise PermissionDeniedError("only a patient account can do this")
        return actor.patient_id

    def _own_prescription(self, patient_id: str, prescription_id: str) -> Prescription:
        rx = self.repos.prescriptions.find(prescription_id)
        if rx is None or rx.patient_id != patient_id:
            raise NotFoundError(f"Prescription {prescription_id} not found")
        return rx

    def _own_refill(self, patient_id: str, refill_id: str) -> RefillRequest:
        refill = self.repos.refills.find(refill_id)
        if refill is None or refill.patient_id != patient_id:
            raise NotFoundError(f"Refill request {refill_id} not found")
        return refill
