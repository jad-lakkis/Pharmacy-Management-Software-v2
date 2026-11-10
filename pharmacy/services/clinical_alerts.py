"""Clinical screening (US4.1) and documented alert resolution (US4.2)."""

from datetime import timedelta
from typing import Any, Optional

from ..domain.enums import (
    AlertOutcome,
    AlertSeverity,
    AlertStatus,
    AlertType,
    AllergySeverity,
    Permission,
    PrescriptionStatus,
)
from ..domain.models import AlertResolution, ClinicalAlert, InteractionRule, Prescription, User
from ..errors import InvalidTransitionError
from .context import Service, new_id
from .validation import require_enum, require_text

P = Permission
MIN_RATIONALE_LENGTH = 10

# Shown in the Figma "Info: Making a form" screen.
RESOLUTION_GUIDANCE = {
    AlertOutcome.OVERRIDE_CONTINUE.value: "Clinically acceptable as written. Explain why the benefit outweighs the risk and any monitoring advised.",
    AlertOutcome.PRESCRIBER_CONFIRMED.value: "Prescriber was contacted and confirmed the order. Record who you spoke to and when.",
    AlertOutcome.PRESCRIBER_CHANGED_THERAPY.value: "Prescriber changed the therapy. The prescription must be returned for correction and re-screened.",
    AlertOutcome.DISCONTINUE.value: "Do not dispense. The prescription must be cancelled.",
}

_PENDING = {
    PrescriptionStatus.RECEIVED,
    PrescriptionStatus.DATA_ENTERED,
    PrescriptionStatus.AWAITING_VERIFICATION,
    PrescriptionStatus.VERIFIED,
    PrescriptionStatus.ON_HOLD,
}


class ClinicalAlertService(Service):
    # ---- screening (internal, called by the prescription workflow) ------
    def screen(self, actor: User, rx: Prescription) -> list[ClinicalAlert]:
        """Supersede any open alerts for ``rx`` and run every check again."""
        now = self.clock.now()
        for old in self.repos.alerts.open_for_prescription(rx.id):
            old.status = AlertStatus.SUPERSEDED
            self.repos.alerts.update(old)

        patient = self.repos.patients.get(rx.patient_id)
        product = self.repos.products.get(rx.product_id)
        ingredients = set(product.ingredients)
        alerts: list[ClinicalAlert] = []

        def add(alert_type, severity, message, triggering, reference):
            alerts.append(ClinicalAlert(
                id=new_id("ALR"), prescription_id=rx.id, patient_id=rx.patient_id, alert_type=alert_type,
                severity=severity, message=message, triggering_data={k: str(v) for k, v in triggering.items()},
                reference_basis=reference, created_at=now,
            ))

        # 1. Allergy
        for allergy in patient.allergies:
            if allergy.substance in ingredients:
                add(
                    AlertType.ALLERGY,
                    AlertSeverity.HIGH if allergy.severity == AllergySeverity.SEVERE else AlertSeverity.MODERATE,
                    f"{patient.full_name} has a recorded {allergy.severity.value.lower()} allergy to {allergy.substance}, "
                    f"an ingredient of {product.name}",
                    {"allergy": allergy.substance, "reaction": allergy.reaction, "allergy_severity": allergy.severity.value,
                     "product": product.name, "matching_ingredient": allergy.substance},
                    f"Patient allergy record, entered {allergy.recorded_at.date().isoformat()}",
                )

        # 2/3. Interactions and duplicate therapy against active medicines
        for other in self.active_medications(rx):
            other_product = self.repos.products.find(other.product_id)
            if other_product is None:
                continue
            for rule in self.repos.interaction_rules.between(ingredients, other_product.ingredients):
                add(
                    AlertType.INTERACTION, rule.severity,
                    f"{product.name} interacts with {other_product.name} ({other.id}): {rule.description}",
                    {"new_ingredient": rule.ingredient_a if rule.ingredient_a in ingredients else rule.ingredient_b,
                     "active_ingredient": rule.ingredient_b if rule.ingredient_a in ingredients else rule.ingredient_a,
                     "active_prescription": other.id, "active_medication": other_product.name,
                     "active_status": other.status.value},
                    rule.reference_source,
                )
            shared = ingredients & set(other_product.ingredients)
            if shared:
                add(
                    AlertType.DUPLICATE_THERAPY, AlertSeverity.MODERATE,
                    f"{product.name} duplicates {', '.join(sorted(shared))} already supplied by {other.id}",
                    {"shared_ingredients": ", ".join(sorted(shared)), "active_prescription": other.id,
                     "active_medication": other_product.name, "active_status": other.status.value},
                    "Pharmacy policy: one active supply per ingredient unless the prescriber confirms",
                )

        # 4. Daily dose limit
        if product.max_daily_quantity is not None and rx.daily_quantity > product.max_daily_quantity:
            add(
                AlertType.DOSE_LIMIT, AlertSeverity.HIGH,
                f"Directed {rx.daily_quantity} units/day exceeds the {product.max_daily_quantity} units/day limit for {product.name}",
                {"directed_daily_quantity": rx.daily_quantity, "max_daily_quantity": product.max_daily_quantity,
                 "directions": rx.directions},
                f"Product labelling maximum daily dose for {product.name} {product.strength}",
            )

        for alert in alerts:
            self.repos.alerts.add(alert)
        self.audit.record(actor, "PRESCRIPTION_SCREENED", "Prescription", rx.id,
                          {"alerts": [f"{a.alert_type.value}:{a.severity.value}" for a in alerts]})
        return alerts

    def active_medications(self, rx: Prescription) -> list[Prescription]:
        """Other prescriptions for the patient that are still being taken or
        in progress. Fills of the same original prescription are excluded,
        so a refill is not flagged as duplicating itself."""
        now = self.clock.now()
        active = []
        for other in self.repos.prescriptions.for_patient(rx.patient_id):
            if other.id == rx.id or other.chain_root_id == rx.chain_root_id:
                continue
            if other.status in _PENDING:
                active.append(other)
            elif other.status in (PrescriptionStatus.DISPENSED, PrescriptionStatus.PICKED_UP) and other.dispensed_at:
                if other.dispensed_at + timedelta(days=other.days_supply) >= now:
                    active.append(other)
        return active

    # ---- review (US4.1, US4.2) ------------------------------------------
    def list_alerts(
        self, actor: User, prescription_id: Optional[str] = None, status: Optional[str] = None
    ) -> list[ClinicalAlert]:
        self.access.require_any(actor, P.ALERT_RESOLVE, P.RX_VERIFY)
        wanted = require_enum(AlertStatus, status, "status") if status else None
        alerts = self.repos.alerts.filter(
            lambda a: (prescription_id is None or a.prescription_id == prescription_id)
            and (wanted is None or a.status == wanted)
        )
        return sorted(alerts, key=lambda a: a.created_at, reverse=True)

    def resolve(self, actor: User, alert_id: str, outcome: Any, rationale: Any) -> ClinicalAlert:
        self.access.require(actor, P.ALERT_RESOLVE)
        alert = self.repos.alerts.get(alert_id)
        if alert.status != AlertStatus.OPEN:
            raise InvalidTransitionError(f"alert {alert.id} is {alert.status.value.lower()}, not open")
        outcome = require_enum(AlertOutcome, outcome, "outcome")
        rationale = require_text(rationale, "rationale", 1000, MIN_RATIONALE_LENGTH)
        alert.status = AlertStatus.RESOLVED
        alert.resolution = AlertResolution(outcome=outcome, rationale=rationale,
                                           resolved_by=actor.id, resolved_at=self.clock.now())
        self.repos.alerts.update(alert)
        self.audit.record(actor, "ALERT_RESOLVED", "ClinicalAlert", alert.id, {
            "prescription_id": alert.prescription_id, "alert_type": alert.alert_type.value,
            "severity": alert.severity.value, "outcome": outcome.value, "rationale": rationale,
        })
        return alert

    def guidance(self) -> dict[str, str]:
        return dict(RESOLUTION_GUIDANCE)

    def list_interaction_rules(self, actor: User) -> list[InteractionRule]:
        self.access.require_any(actor, P.ALERT_RESOLVE, P.RX_VERIFY)
        return self.repos.interaction_rules.list()
