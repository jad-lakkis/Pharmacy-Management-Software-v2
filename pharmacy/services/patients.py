"""Patient and prescriber profiles (US2.1) and the clinical view (US2.2)."""

from typing import Any, Optional

from ..domain.enums import AllergySeverity, Permission
from ..domain.models import Allergy, Patient, Prescriber, User
from ..errors import ConflictError, NotFoundError, ValidationError
from .context import Service, new_id, newest_first
from .validation import (
    optional_email,
    optional_phone,
    optional_text,
    require_date,
    require_enum,
    require_text,
)

P = Permission

PATIENT_EDITABLE = ("first_name", "last_name", "date_of_birth", "phone", "email", "address")
PRESCRIBER_EDITABLE = ("name", "license_number", "phone", "specialty", "clinic")


class PatientService(Service):
    # ---- patients --------------------------------------------------------
    def create_patient(
        self,
        actor: User,
        first_name: str,
        last_name: str,
        date_of_birth: Any,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        address: Optional[str] = None,
    ) -> Patient:
        self.access.require(actor, P.PATIENT_WRITE)
        fields = self._clean_patient_fields(
            {"first_name": first_name, "last_name": last_name, "date_of_birth": date_of_birth,
             "phone": phone, "email": email, "address": address}
        )
        if self.repos.patients.by_name_and_dob(fields["first_name"], fields["last_name"], fields["date_of_birth"]):
            raise ConflictError("a patient with the same name and date of birth already exists")
        now = self.clock.now()
        patient = Patient(id=new_id("PAT"), created_at=now, updated_at=now, **fields)
        self.repos.patients.add(patient)
        self.audit.record(actor, "PATIENT_CREATED", "Patient", patient.id, {"name": patient.full_name})
        return patient

    def update_patient(self, actor: User, patient_id: str, **changes: Any) -> Patient:
        self.access.require(actor, P.PATIENT_WRITE)
        unknown = set(changes) - set(PATIENT_EDITABLE)
        if unknown:
            raise ValidationError(f"cannot update: {', '.join(sorted(unknown))}")
        patient = self.repos.patients.get(patient_id)
        merged = {k: getattr(patient, k) for k in PATIENT_EDITABLE}
        merged.update(changes)
        fields = self._clean_patient_fields(merged)
        duplicate = self.repos.patients.by_name_and_dob(fields["first_name"], fields["last_name"], fields["date_of_birth"])
        if duplicate and duplicate.id != patient.id:
            raise ConflictError("a patient with the same name and date of birth already exists")
        changed = {k: [str(getattr(patient, k)), str(v)] for k, v in fields.items() if getattr(patient, k) != v}
        for key, value in fields.items():
            setattr(patient, key, value)
        patient.updated_at = self.clock.now()
        self.repos.patients.update(patient)
        self.audit.record(actor, "PATIENT_UPDATED", "Patient", patient.id, {"changed": changed})
        return patient

    def get_patient(self, actor: User, patient_id: str) -> Patient:
        self.access.require(actor, P.PATIENT_READ)
        return self.repos.patients.get(patient_id)

    def search_patients(self, actor: User, query: str = "") -> list[Patient]:
        self.access.require(actor, P.PATIENT_READ)
        return self.repos.patients.search(query or "")

    def add_allergy(self, actor: User, patient_id: str, substance: str, reaction: str, severity: Any) -> Patient:
        """Add an allergy, or update it if the substance is already recorded."""
        self.access.require(actor, P.PATIENT_WRITE)
        patient = self.repos.patients.get(patient_id)
        substance = require_text(substance, "substance", 80).lower()
        reaction = require_text(reaction, "reaction", 200)
        severity = require_enum(AllergySeverity, severity, "severity")
        patient.allergies = [a for a in patient.allergies if a.substance != substance]
        patient.allergies.append(
            Allergy(substance=substance, reaction=reaction, severity=severity,
                    recorded_at=self.clock.now(), recorded_by=actor.id)
        )
        patient.updated_at = self.clock.now()
        self.repos.patients.update(patient)
        self.audit.record(actor, "ALLERGY_RECORDED", "Patient", patient.id,
                          {"substance": substance, "severity": severity.value})
        return patient

    def remove_allergy(self, actor: User, patient_id: str, substance: str) -> Patient:
        self.access.require(actor, P.PATIENT_WRITE)
        patient = self.repos.patients.get(patient_id)
        substance = require_text(substance, "substance", 80).lower()
        remaining = [a for a in patient.allergies if a.substance != substance]
        if len(remaining) == len(patient.allergies):
            raise NotFoundError(f"no allergy to '{substance}' is recorded")
        patient.allergies = remaining
        patient.updated_at = self.clock.now()
        self.repos.patients.update(patient)
        self.audit.record(actor, "ALLERGY_REMOVED", "Patient", patient.id, {"substance": substance})
        return patient

    def clinical_profile(self, actor: User, patient_id: str) -> dict[str, Any]:
        """US2.2: allergies plus full medication history, newest first."""
        self.access.require(actor, P.CLINICAL_READ)
        patient = self.repos.patients.get(patient_id)
        history = []
        for rx in newest_first(self.repos.prescriptions.for_patient(patient_id), lambda r: r.created_at):
            product = self.repos.products.find(rx.product_id)
            prescriber = self.repos.prescribers.find(rx.prescriber_id)
            history.append({
                "prescription_id": rx.id,
                "medication": product.name if product else rx.product_id,
                "strength": product.strength if product else None,
                "ingredients": product.ingredients if product else [],
                "quantity": rx.quantity,
                "directions": rx.directions,
                "status": rx.status.value,
                "written_date": rx.written_date.isoformat(),
                "dispensed_at": rx.dispensed_at.isoformat() if rx.dispensed_at else None,
                "prescriber": prescriber.name if prescriber else rx.prescriber_id,
                "refills_remaining": rx.refills_remaining,
                "is_refill_fill": rx.original_prescription_id is not None,
            })
        self.audit.record(actor, "CLINICAL_PROFILE_VIEWED", "Patient", patient.id)
        return {"patient": patient, "allergies": patient.allergies, "medication_history": history}

    def _clean_patient_fields(self, raw: dict[str, Any]) -> dict[str, Any]:
        dob = require_date(raw.get("date_of_birth"), "date of birth")
        if dob > self.clock.today():
            raise ValidationError("date of birth cannot be in the future")
        if dob.year < 1900:
            raise ValidationError("date of birth is not plausible")
        return {
            "first_name": require_text(raw.get("first_name"), "first name", 60),
            "last_name": require_text(raw.get("last_name"), "last name", 60),
            "date_of_birth": dob,
            "phone": optional_phone(raw.get("phone")),
            "email": optional_email(raw.get("email")),
            "address": optional_text(raw.get("address"), "address", 200),
        }

    # ---- prescribers -----------------------------------------------------
    def create_prescriber(
        self,
        actor: User,
        name: str,
        license_number: str,
        phone: Optional[str] = None,
        specialty: Optional[str] = None,
        clinic: Optional[str] = None,
    ) -> Prescriber:
        self.access.require(actor, P.PATIENT_WRITE)
        fields = self._clean_prescriber_fields(
            {"name": name, "license_number": license_number, "phone": phone, "specialty": specialty, "clinic": clinic}
        )
        if self.repos.prescribers.by_license(fields["license_number"]):
            raise ConflictError(f"licence {fields['license_number']} is already registered")
        now = self.clock.now()
        prescriber = Prescriber(id=new_id("PRS"), created_at=now, updated_at=now, **fields)
        self.repos.prescribers.add(prescriber)
        self.audit.record(actor, "PRESCRIBER_CREATED", "Prescriber", prescriber.id,
                          {"name": prescriber.name, "license_number": prescriber.license_number})
        return prescriber

    def update_prescriber(self, actor: User, prescriber_id: str, **changes: Any) -> Prescriber:
        self.access.require(actor, P.PATIENT_WRITE)
        unknown = set(changes) - set(PRESCRIBER_EDITABLE)
        if unknown:
            raise ValidationError(f"cannot update: {', '.join(sorted(unknown))}")
        prescriber = self.repos.prescribers.get(prescriber_id)
        merged = {k: getattr(prescriber, k) for k in PRESCRIBER_EDITABLE}
        merged.update(changes)
        fields = self._clean_prescriber_fields(merged)
        other = self.repos.prescribers.by_license(fields["license_number"])
        if other and other.id != prescriber.id:
            raise ConflictError(f"licence {fields['license_number']} is already registered")
        changed = {k: [str(getattr(prescriber, k)), str(v)] for k, v in fields.items() if getattr(prescriber, k) != v}
        for key, value in fields.items():
            setattr(prescriber, key, value)
        prescriber.updated_at = self.clock.now()
        self.repos.prescribers.update(prescriber)
        self.audit.record(actor, "PRESCRIBER_UPDATED", "Prescriber", prescriber.id, {"changed": changed})
        return prescriber

    def list_prescribers(self, actor: User) -> list[Prescriber]:
        self.access.require(actor, P.PATIENT_READ)
        return self.repos.prescribers.list()

    @staticmethod
    def _clean_prescriber_fields(raw: dict[str, Any]) -> dict[str, Any]:
        license_number = require_text(raw.get("license_number"), "licence number", 30).upper()
        if not license_number.replace("-", "").isalnum():
            raise ValidationError("licence number may contain letters, digits and '-' only")
        return {
            "name": require_text(raw.get("name"), "name", 80),
            "license_number": license_number,
            "phone": optional_phone(raw.get("phone")),
            "specialty": optional_text(raw.get("specialty"), "specialty", 60),
            "clinic": optional_text(raw.get("clinic"), "clinic", 100),
        }
