"""Notification channel and consent (US7.2), plus the simulated outbox."""

from typing import Any, Optional

from ..domain.enums import ConsentAction, NotificationChannel, NotificationStatus, Permission, Role
from ..domain.models import ConsentEvent, Notification, NotificationPreference, User
from ..errors import PermissionDeniedError, ValidationError
from .context import Service, new_id, newest_first
from .validation import require_enum

P = Permission

POLICY_VERSION = "2026-11"
POLICY_TEXT = (
    "We use your contact details only to tell you about your prescriptions and refills "
    "(for example: request received, ready for pickup, or a question from the pharmacist). "
    "We never use them for marketing. You can change your channel or withdraw consent at any time; "
    "withdrawing stops all messages, and you can still track your requests in the app."
)


def _mask(value: str) -> str:
    if "@" in value:
        name, domain = value.split("@", 1)
        return f"{name[:1]}***@{domain}"
    digits = [c for c in value if c.isdigit()]
    return "***" + "".join(digits[-3:])


class NotificationService(Service):
    def policy(self) -> dict[str, str]:
        return {"version": POLICY_VERSION, "text": POLICY_TEXT}

    # ---- patient self-service -------------------------------------------
    def get_preferences(self, actor: User) -> NotificationPreference:
        patient_id = self._own_patient(actor)
        return self._prefs(patient_id)

    def set_channel(self, actor: User, channel: Any) -> NotificationPreference:
        patient_id = self._own_patient(actor)
        channel = require_enum(NotificationChannel, channel, "channel")
        self._destination(patient_id, channel)  # validates contact details exist
        prefs = self._prefs(patient_id)
        if prefs.channel == channel:
            return prefs
        previous = prefs.channel
        prefs.channel = channel
        prefs.history.append(ConsentEvent(ConsentAction.CHANNEL_CHANGED, self.clock.now(), channel, prefs.policy_version))
        self.repos.notification_preferences.save(prefs)
        self.audit.record(actor, "NOTIFICATION_CHANNEL_CHANGED", "Patient", patient_id,
                          {"from": previous.value if previous else None, "to": channel.value})
        return prefs

    def give_consent(self, actor: User, policy_version: Any) -> NotificationPreference:
        patient_id = self._own_patient(actor)
        if policy_version != POLICY_VERSION:
            raise ValidationError(f"please review and accept the current policy (version {POLICY_VERSION})")
        prefs = self._prefs(patient_id)
        if prefs.channel is None:
            raise ValidationError("choose a notification channel before giving consent")
        prefs.consent_given = True
        prefs.policy_version = POLICY_VERSION
        prefs.history.append(ConsentEvent(ConsentAction.GIVEN, self.clock.now(), prefs.channel, POLICY_VERSION))
        self.repos.notification_preferences.save(prefs)
        self.audit.record(actor, "CONSENT_GIVEN", "Patient", patient_id,
                          {"channel": prefs.channel.value, "policy_version": POLICY_VERSION})
        return prefs

    def withdraw_consent(self, actor: User) -> NotificationPreference:
        patient_id = self._own_patient(actor)
        prefs = self._prefs(patient_id)
        if not prefs.consent_given:
            return prefs
        prefs.consent_given = False
        prefs.history.append(ConsentEvent(ConsentAction.WITHDRAWN, self.clock.now(), prefs.channel, prefs.policy_version))
        self.repos.notification_preferences.save(prefs)
        self.audit.record(actor, "CONSENT_WITHDRAWN", "Patient", patient_id)
        return prefs

    def list_notifications(self, actor: User) -> list[Notification]:
        patient_id = self._own_patient(actor)
        return newest_first(self.repos.notifications.for_patient(patient_id), lambda n: n.created_at)

    # ---- sending (internal) ---------------------------------------------
    def notify(self, patient_id: str, subject: str, message: str) -> Notification:
        """Send through the patient's channel only if they consented;
        otherwise record the message as suppressed, with the reason."""
        prefs = self._prefs(patient_id)
        status, reason, destination = NotificationStatus.SENT, None, None
        if not prefs.consent_given:
            status, reason = NotificationStatus.SUPPRESSED, "no consent on file"
        elif prefs.channel is None:
            status, reason = NotificationStatus.SUPPRESSED, "no channel selected"
        else:
            try:
                destination = self._destination(patient_id, prefs.channel)
                if prefs.channel != NotificationChannel.IN_APP:
                    destination = _mask(destination)
            except ValidationError as exc:
                status, reason = NotificationStatus.SUPPRESSED, exc.message
        notification = Notification(
            id=new_id("NTF"), patient_id=patient_id, subject=subject, message=message, status=status,
            created_at=self.clock.now(), channel=prefs.channel if status == NotificationStatus.SENT else None,
            destination=destination, suppressed_reason=reason,
        )
        self.repos.notifications.add(notification)
        return notification

    # ---- helpers ---------------------------------------------------------
    def _own_patient(self, actor: User) -> str:
        self.access.require(actor, P.NOTIFY_PREFS)
        if actor.role != Role.PATIENT or not actor.patient_id:
            raise PermissionDeniedError("only a patient account can manage its own notifications")
        return actor.patient_id

    def _prefs(self, patient_id: str) -> NotificationPreference:
        prefs = self.repos.notification_preferences.find(patient_id)
        return prefs or NotificationPreference(id=patient_id, patient_id=patient_id)

    def _destination(self, patient_id: str, channel: NotificationChannel) -> str:
        patient = self.repos.patients.get(patient_id)
        if channel == NotificationChannel.SMS:
            if not patient.phone:
                raise ValidationError("add a phone number to your profile to use SMS")
            return patient.phone
        if channel == NotificationChannel.EMAIL:
            if not patient.email:
                raise ValidationError("add an email address to your profile to use email")
            return patient.email
        return "in-app inbox"
