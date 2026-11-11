"""Users, roles, permissions and the permitted dashboard (US1.1, US1.2)."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Optional

from ..domain.enums import PATIENT_ONLY_PERMISSIONS, Permission, Role
from ..domain.models import User
from ..errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from .context import Service, new_id
from .validation import require_enum, require_text

P = Permission

ROLE_DEFAULTS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset({
        P.MANAGE_USERS, P.REPORTS_VIEW, P.AUDIT_VIEW, P.PURCHASE_VIEW, P.PURCHASE_DECIDE,
        P.INVENTORY_VIEW, P.INVENTORY_ALERTS, P.PRODUCT_MANAGE,
    }),
    Role.MANAGER: frozenset({
        P.INVENTORY_VIEW, P.INVENTORY_ALERTS, P.PRODUCT_MANAGE, P.PURCHASE_VIEW,
        P.PURCHASE_DECIDE, P.REPORTS_VIEW,
    }),
    Role.PHARMACIST: frozenset({
        P.PATIENT_READ, P.PATIENT_WRITE, P.CLINICAL_READ, P.RX_VIEW, P.RX_ENTER, P.RX_VERIFY,
        P.RX_DISPENSE, P.ALERT_RESOLVE, P.REFILL_REVIEW, P.INVENTORY_VIEW,
    }),
    Role.TECHNICIAN: frozenset({
        P.PATIENT_READ, P.PATIENT_WRITE, P.RX_VIEW, P.RX_ENTER, P.RX_DISPENSE,
        P.INVENTORY_VIEW, P.INVENTORY_RECEIVE,
    }),
    Role.CASHIER: frozenset({P.RX_VIEW, P.SALES_RECORD}),
    Role.PATIENT: frozenset({P.REFILL_REQUEST, P.NOTIFY_PREFS}),
}


@dataclass(frozen=True)
class DashboardTask:
    key: str
    label: str
    permission: Permission
    description: str


# Labels follow the Figma home screens; order is the order shown.
DASHBOARD_TASKS: tuple[DashboardTask, ...] = (
    DashboardTask("access", "Modify access", P.MANAGE_USERS, "Add members, change roles and permissions, deactivate users"),
    DashboardTask("patients", "Enter data", P.PATIENT_WRITE, "Create and update patient and prescriber profiles"),
    DashboardTask("patient_record", "Search Patient Record", P.CLINICAL_READ, "Allergies and medication history"),
    DashboardTask("workflow", "Workflow management", P.RX_ENTER, "Enter prescriptions and move them through stages"),
    DashboardTask("verification", "ALERTS", P.RX_VERIFY, "Verify prescriptions and review clinical alerts"),
    DashboardTask("alert_form", "Decision-making form", P.ALERT_RESOLVE, "Record how a clinical alert was resolved"),
    DashboardTask("refill_review", "Refill requests", P.REFILL_REVIEW, "Review patient refill requests"),
    DashboardTask("receive", "Check by Bar Code", P.INVENTORY_RECEIVE, "Scan lot and expiry when receiving stock"),
    DashboardTask("inventory", "Inventory", P.INVENTORY_VIEW, "Stock on hand by lot"),
    DashboardTask("inventory_alerts", "Stock alerts", P.INVENTORY_ALERTS, "Low-stock and near-expiry alerts"),
    DashboardTask("reorder", "Recommended: Reorder", P.PURCHASE_VIEW, "Reorder suggestions with their calculation"),
    DashboardTask("purchase_orders", "Purchase Inventory", P.PURCHASE_DECIDE, "Approve, modify or reject purchase orders"),
    DashboardTask("suppliers", "Suppliers", P.PURCHASE_VIEW, "Supplier contacts and price lists"),
    DashboardTask("orders", "See orders", P.SALES_RECORD, "Dispensed prescriptions waiting for payment"),
    DashboardTask("sales", "Record Sales", P.SALES_RECORD, "Record payments and over-the-counter sales"),
    DashboardTask("receipts", "Issue Receipts", P.SALES_RECORD, "Find and reprint receipts"),
    DashboardTask("reports", "Reports", P.REPORTS_VIEW, "Sales and operations reports"),
    DashboardTask("audit", "Audit log", P.AUDIT_VIEW, "Timestamped audit trail"),
    DashboardTask("refills", "Request a refill", P.REFILL_REQUEST, "Request a refill and track its status"),
    DashboardTask("notifications", "Select notifications", P.NOTIFY_PREFS, "Choose how the pharmacy contacts you"),
    DashboardTask("consent", "Consent and Policies", P.NOTIFY_PREFS, "Give or withdraw communication consent"),
)

PBKDF2_ITERATIONS = 120_000
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return digest.hex(), salt


def _check_password_strength(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


class AccessService(Service):
    # ---- authorisation ---------------------------------------------------
    def effective_permissions(self, user: User) -> frozenset[Permission]:
        if not user.active:
            return frozenset()
        perms = set(ROLE_DEFAULTS[user.role]) | set(user.granted_permissions)
        perms -= set(user.revoked_permissions)
        return frozenset(perms)

    def has(self, user: User, permission: Permission) -> bool:
        return permission in self.effective_permissions(user)

    def require(self, user: User, permission: Permission) -> None:
        if not self.has(user, permission):
            raise PermissionDeniedError(f"{user.display_name} is not allowed to perform '{permission.value}'")

    def require_any(self, user: User, *permissions: Permission) -> None:
        if not any(self.has(user, p) for p in permissions):
            names = " or ".join(p.value for p in permissions)
            raise PermissionDeniedError(f"{user.display_name} needs {names}")

    def require_staff(self, user: User) -> None:
        if not user.active or user.role == Role.PATIENT:
            raise PermissionDeniedError("Staff access required")

    def dashboard(self, user: User) -> list[DashboardTask]:
        """US1.2: only tasks the user is permitted to perform."""
        perms = self.effective_permissions(user)
        return [t for t in DASHBOARD_TASKS if t.permission in perms]

    # ---- authentication --------------------------------------------------
    def authenticate(self, username: str, password: str) -> Optional[User]:
        """Return the user for valid credentials, else ``None``.

        Failures are audited without saying whether the username exists.
        """
        user = self.repos.users.by_username(username or "")
        ok = False
        if user is not None and user.active and isinstance(password, str):
            candidate, _ = hash_password(password, user.password_salt)
            ok = hmac.compare_digest(candidate, user.password_hash)
        if not ok:
            self.audit.record(None, "LOGIN_FAILED", "User", None, {"username": (username or "")[:64]})
            return None
        self.audit.record(user, "LOGIN", "User", user.id)
        return user

    # ---- user administration (US1.1) -----------------------------------
    def list_users(self, actor: User) -> list[User]:
        self.require(actor, P.MANAGE_USERS)
        return self.repos.users.list()

    def get_user(self, actor: User, user_id: str) -> User:
        if actor.id != user_id:
            self.require(actor, P.MANAGE_USERS)
        return self.repos.users.get(user_id)

    def create_user(
        self,
        actor: Optional[User],
        username: str,
        display_name: str,
        role: Role | str,
        password: str,
        patient_id: Optional[str] = None,
    ) -> User:
        """Create a login. ``actor=None`` is only for bootstrap seeding."""
        if actor is not None:
            self.require(actor, P.MANAGE_USERS)
        username = require_text(username, "username", 40, 3).lower()
        if not username.replace(".", "").replace("_", "").replace("-", "").isalnum():
            raise ValidationError("username may contain letters, digits, '.', '_' and '-' only")
        display_name = require_text(display_name, "display name", 80)
        role = require_enum(Role, role, "role")
        _check_password_strength(password)
        if self.repos.users.by_username(username):
            raise ConflictError(f"username '{username}' is already taken")
        if role == Role.PATIENT:
            if not patient_id:
                raise ValidationError("a patient account must be linked to a patient record")
            self.repos.patients.get(patient_id)
            if self.repos.users.by_patient(patient_id):
                raise ConflictError("that patient record already has an account")
        elif patient_id:
            raise ValidationError("only patient accounts can be linked to a patient record")
        pw_hash, salt = hash_password(password)
        user = User(
            id=new_id("USR"),
            username=username,
            display_name=display_name,
            role=role,
            password_hash=pw_hash,
            password_salt=salt,
            patient_id=patient_id if role == Role.PATIENT else None,
            created_at=self.clock.now(),
        )
        self.repos.users.add(user)
        self.audit.record(actor, "USER_CREATED", "User", user.id, {"username": username, "role": role.value})
        return user

    def change_role(self, actor: User, user_id: str, role: Role | str) -> User:
        """Change a staff member's role. Individual overrides are cleared,
        because they were granted relative to the old role."""
        self.require(actor, P.MANAGE_USERS)
        role = require_enum(Role, role, "role")
        user = self.repos.users.get(user_id)
        if (user.role == Role.PATIENT) != (role == Role.PATIENT):
            raise ValidationError("patient and staff accounts cannot be converted into each other")
        old = user.role
        user.role = role
        user.granted_permissions, user.revoked_permissions = [], []
        self._guard_admin_remains(user)
        self.repos.users.update(user)
        self.audit.record(actor, "ROLE_CHANGED", "User", user.id, {"from": old.value, "to": role.value})
        return user

    def grant_permission(self, actor: User, user_id: str, permission: Permission | str) -> User:
        self.require(actor, P.MANAGE_USERS)
        permission = require_enum(Permission, permission, "permission")
        user = self.repos.users.get(user_id)
        self._check_permission_fits_account(user, permission)
        if permission in user.revoked_permissions:
            user.revoked_permissions.remove(permission)
        if permission not in ROLE_DEFAULTS[user.role] and permission not in user.granted_permissions:
            user.granted_permissions.append(permission)
        self.repos.users.update(user)
        self.audit.record(actor, "PERMISSION_GRANTED", "User", user.id, {"permission": permission.value})
        return user

    def revoke_permission(self, actor: User, user_id: str, permission: Permission | str) -> User:
        self.require(actor, P.MANAGE_USERS)
        permission = require_enum(Permission, permission, "permission")
        user = self.repos.users.get(user_id)
        if permission in user.granted_permissions:
            user.granted_permissions.remove(permission)
        if permission in ROLE_DEFAULTS[user.role] and permission not in user.revoked_permissions:
            user.revoked_permissions.append(permission)
        self._guard_admin_remains(user)
        self.repos.users.update(user)
        self.audit.record(actor, "PERMISSION_REVOKED", "User", user.id, {"permission": permission.value})
        return user

    def deactivate_user(self, actor: User, user_id: str) -> User:
        """Figma "Delete Users": a soft delete that keeps audit references."""
        self.require(actor, P.MANAGE_USERS)
        if actor.id == user_id:
            raise ValidationError("you cannot deactivate your own account")
        user = self.repos.users.get(user_id)
        if not user.active:
            return user
        user.active = False
        self._guard_admin_remains(user)
        self.repos.users.update(user)
        self.audit.record(actor, "USER_DEACTIVATED", "User", user.id, {"username": user.username})
        return user

    def reactivate_user(self, actor: User, user_id: str) -> User:
        self.require(actor, P.MANAGE_USERS)
        user = self.repos.users.get(user_id)
        if user.active:
            return user
        user.active = True
        self.repos.users.update(user)
        self.audit.record(actor, "USER_REACTIVATED", "User", user.id, {"username": user.username})
        return user

    def reset_password(self, actor: User, user_id: str, new_password: str) -> User:
        if actor.id != user_id:
            self.require(actor, P.MANAGE_USERS)
        _check_password_strength(new_password)
        user = self.repos.users.get(user_id)
        user.password_hash, user.password_salt = hash_password(new_password)
        self.repos.users.update(user)
        self.audit.record(actor, "PASSWORD_RESET", "User", user.id)
        return user

    # ---- helpers ---------------------------------------------------------
    def _check_permission_fits_account(self, user: User, permission: Permission) -> None:
        if user.role == Role.PATIENT and permission not in PATIENT_ONLY_PERMISSIONS:
            raise ValidationError("patient accounts can only hold patient self-service permissions")
        if user.role != Role.PATIENT and permission in PATIENT_ONLY_PERMISSIONS:
            raise ValidationError("patient self-service permissions need a patient account")

    def _guard_admin_remains(self, changed: User) -> None:
        """Refuse a change that leaves nobody able to manage access."""
        for user in self.repos.users.list():
            candidate = changed if user.id == changed.id else user
            if P.MANAGE_USERS in self.effective_permissions(candidate):
                return
        raise ValidationError("at least one active user must keep the access-management permission")


def public_user(user: User) -> dict:
    """User fields that are safe to return through the API."""
    if user is None:
        raise NotFoundError("user not found")
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role.value,
        "active": user.active,
        "granted_permissions": [p.value for p in user.granted_permissions],
        "revoked_permissions": [p.value for p in user.revoked_permissions],
        "patient_id": user.patient_id,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }
