"""Transport-independent JSON API.

``Api.handle(method, path, query, body, token)`` returns ``(status, payload)``
and never touches sockets, so it can be exercised directly in tests.
``server.py`` adapts it to ``http.server``.
"""

import dataclasses
import re
import secrets
import threading
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional
from urllib.parse import unquote

from ..domain.enums import Permission, Role
from ..domain.models import Prescription, User
from ..errors import AuthenticationError, NotFoundError, PharmacyError, ValidationError
from ..repositories import codec
from ..services import PharmacyApp
from ..services.access import DASHBOARD_TASKS, ROLE_DEFAULTS, public_user
from ..services.refills import BRANCHES

P = Permission
SESSION_TTL = timedelta(hours=8)
MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)

Handler = Callable[["Request"], Any]


@dataclasses.dataclass
class Request:
    user: Optional[User]
    params: dict[str, str]
    query: dict[str, str]
    body: dict[str, Any]
    token: Optional[str] = None

    def q(self, name: str, default: Optional[str] = None) -> Optional[str]:
        value = self.query.get(name)
        return value if value not in (None, "") else default

    def qdate(self, name: str) -> Optional[date]:
        value = self.q(name)
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValidationError(f"{name} must be a date (YYYY-MM-DD)") from None

    def qint(self, name: str, default: int) -> int:
        value = self.q(name)
        if value is None:
            return default
        if not value.isdigit():
            raise ValidationError(f"{name} must be a whole number")
        return int(value)

    def b(self, name: str, default: Any = None) -> Any:
        return self.body.get(name, default)


def serialize(value: Any) -> Any:
    """Like ``codec.to_primitive`` but hides credentials and adds computed
    fields the UI needs."""
    if isinstance(value, User):
        return public_user(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        data = {f.name: serialize(getattr(value, f.name)) for f in dataclasses.fields(value)}
        for prop in ("full_name", "refills_remaining", "days_supply", "total_cents", "total_cost_cents"):
            if hasattr(type(value), prop):
                data[prop] = getattr(value, prop)
        return data
    if isinstance(value, dict):
        return {str(codec.to_primitive(k)): serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [serialize(v) for v in value]
    return codec.to_primitive(value)


class Api:
    def __init__(self, app: PharmacyApp, demo_accounts: Optional[list[dict[str, str]]] = None):
        """``demo_accounts`` is only passed when the server seeded demo data,
        so the login screen can offer one-click demo sign-in."""
        self.app = app
        self._demo_accounts = list(demo_accounts or [])
        self._sessions: dict[str, tuple[str, datetime]] = {}
        self._failed: dict[str, list[datetime]] = {}
        self._lock = threading.Lock()
        self._routes: list[tuple[str, re.Pattern, Handler, bool]] = []
        self._register()

    # ---- routing ---------------------------------------------------------
    def route(self, method: str, pattern: str, handler: Handler, auth: bool = True) -> None:
        regex = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")
        self._routes.append((method, regex, handler, auth))

    def handle(self, method: str, path: str, query: Optional[dict[str, str]] = None,
               body: Optional[dict[str, Any]] = None, token: Optional[str] = None) -> tuple[int, Any]:
        query, body = query or {}, body or {}
        if not isinstance(body, dict):
            return 400, {"error": "request body must be a JSON object"}
        path_matched = False
        for route_method, regex, handler, auth in self._routes:
            match = regex.match(path)
            if not match:
                continue
            path_matched = True
            if route_method != method:
                continue
            try:
                with self.app.transaction():
                    user = self._session_user(token) if auth else None
                    params = {k: unquote(v) for k, v in match.groupdict().items()}
                    result = handler(Request(user, params, query, body, token))
                status = 200
                if isinstance(result, dict) and "_status" in result:
                    result = dict(result)
                    status = result.pop("_status")
                return status, serialize(result)
            except PharmacyError as exc:
                return exc.status, {"error": exc.message, "type": type(exc).__name__}
        if path_matched:
            return 405, {"error": "method not allowed"}
        return 404, {"error": "no such endpoint"}

    # ---- sessions --------------------------------------------------------
    def _session_user(self, token: Optional[str]) -> User:
        if not token:
            raise AuthenticationError("please sign in")
        with self._lock:
            entry = self._sessions.get(token)
            if entry is None or entry[1] < self.app.clock.now():
                self._sessions.pop(token, None)
                raise AuthenticationError("your session has expired; please sign in again")
            user_id = entry[0]
        user = self.app.repos.users.find(user_id)
        if user is None or not user.active:
            raise AuthenticationError("this account is no longer active")
        return user

    def _login(self, req: Request) -> dict[str, Any]:
        username = str(req.b("username", "")).strip().lower()
        now = self.app.clock.now()
        with self._lock:
            recent = [t for t in self._failed.get(username, []) if now - t < LOCKOUT]
            self._failed[username] = recent
            if len(recent) >= MAX_FAILED_LOGINS:
                raise AuthenticationError("too many failed attempts; try again in 15 minutes")
        user = self.app.access.authenticate(username, req.b("password", ""))
        if user is None:
            with self._lock:
                self._failed.setdefault(username, []).append(now)
            # Commit the LOGIN_FAILED audit entry, then report the failure.
            return {"_status": 401, "error": "invalid username or password"}
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._failed.pop(username, None)
            self._sessions[token] = (user.id, now + SESSION_TTL)
        return {"token": token, **self._me(user)}

    def _logout(self, req: Request) -> dict[str, Any]:
        with self._lock:
            self._sessions.pop(req.token or "", None)
        return {"ok": True}

    def _me(self, user: User) -> dict[str, Any]:
        perms = sorted(p.value for p in self.app.access.effective_permissions(user))
        return {
            "user": public_user(user),
            "permissions": perms,
            "dashboard": [dataclasses.asdict(t) | {"permission": t.permission.value}
                          for t in self.app.access.dashboard(user)],
        }

    # ---- view helpers ----------------------------------------------------
    def _rx_view(self, user: User, rx: Prescription) -> dict[str, Any]:
        repos = self.app.repos
        product = repos.products.find(rx.product_id)
        patient = repos.patients.find(rx.patient_id)
        prescriber = repos.prescribers.find(rx.prescriber_id)
        data = serialize(rx)
        data["medication"] = f"{product.name} {product.strength}" if product else rx.product_id
        data["patient_name"] = patient.full_name if patient else rx.patient_id
        data["prescriber_name"] = prescriber.name if prescriber else rx.prescriber_id
        data["allowed_transitions"] = [s.value for s in self.app.prescriptions.allowed_transitions(user, rx)]
        if self.app.access.has(user, P.RX_VERIFY) or self.app.access.has(user, P.ALERT_RESOLVE):
            data["alerts"] = serialize([a for a in repos.alerts.for_prescription(rx.id) if a.status.value != "SUPERSEDED"])
            data["verification_blockers"] = self.app.prescriptions.verification_blockers(rx)
        return data

    # ---- endpoints -------------------------------------------------------
    def _register(self) -> None:  # noqa: C901 - a flat route table reads best
        a = self.app
        r = self.route

        # Session
        r("POST", "/api/login", self._login, auth=False)
        r("POST", "/api/logout", self._logout)
        r("GET", "/api/me", lambda q: self._me(q.user))
        r("GET", "/api/branches", lambda q: list(BRANCHES), auth=False)
        r("GET", "/api/demo-accounts", lambda q: self._demo_accounts, auth=False)
        r("GET", "/api/policy", lambda q: a.notifications.policy(), auth=False)

        # Access control (US1.1, US1.2)
        r("GET", "/api/users", lambda q: a.access.list_users(q.user))
        r("POST", "/api/users", lambda q: a.access.create_user(
            q.user, q.b("username"), q.b("display_name"), q.b("role"), q.b("password"), q.b("patient_id")))
        r("POST", "/api/users/{id}/role", lambda q: a.access.change_role(q.user, q.params["id"], q.b("role")))
        r("POST", "/api/users/{id}/permissions", self._change_permission)
        r("POST", "/api/users/{id}/deactivate", lambda q: a.access.deactivate_user(q.user, q.params["id"]))
        r("POST", "/api/users/{id}/reactivate", lambda q: a.access.reactivate_user(q.user, q.params["id"]))
        r("POST", "/api/users/{id}/password", lambda q: a.access.reset_password(q.user, q.params["id"], q.b("password")))
        r("GET", "/api/permissions", self._permission_catalog)

        # Patients and prescribers (US2.1, US2.2)
        r("GET", "/api/patients", lambda q: a.patients.search_patients(q.user, q.q("q", "")))
        r("POST", "/api/patients", lambda q: a.patients.create_patient(
            q.user, q.b("first_name"), q.b("last_name"), q.b("date_of_birth"), q.b("phone"), q.b("email"), q.b("address")))
        r("GET", "/api/patients/{id}", lambda q: a.patients.get_patient(q.user, q.params["id"]))
        r("PATCH", "/api/patients/{id}", lambda q: a.patients.update_patient(q.user, q.params["id"], **q.body))
        r("POST", "/api/patients/{id}/allergies", lambda q: a.patients.add_allergy(
            q.user, q.params["id"], q.b("substance"), q.b("reaction"), q.b("severity")))
        r("DELETE", "/api/patients/{id}/allergies/{substance}", lambda q: a.patients.remove_allergy(
            q.user, q.params["id"], q.params["substance"]))
        r("GET", "/api/patients/{id}/clinical", lambda q: a.patients.clinical_profile(q.user, q.params["id"]))
        r("GET", "/api/prescribers", lambda q: a.patients.list_prescribers(q.user))
        r("POST", "/api/prescribers", lambda q: a.patients.create_prescriber(
            q.user, q.b("name"), q.b("license_number"), q.b("phone"), q.b("specialty"), q.b("clinic")))
        r("PATCH", "/api/prescribers/{id}", lambda q: a.patients.update_prescriber(q.user, q.params["id"], **q.body))

        # Products and inventory (US5.1, US5.2)
        r("GET", "/api/products", lambda q: a.inventory.list_products(q.user))
        r("POST", "/api/products", lambda q: a.inventory.add_product(
            q.user, q.b("name"), q.b("gtin"), q.b("ingredients"), q.b("strength"), q.b("dosage_form"),
            q.b("unit_price_cents"), q.b("unit_cost_cents"), q.b("requires_prescription", True),
            q.b("reorder_point", 0), q.b("safety_stock", 0), q.b("lead_time_days", 7), q.b("max_daily_quantity")))
        r("PATCH", "/api/products/{id}/reorder", lambda q: a.inventory.update_reorder_settings(
            q.user, q.params["id"], q.b("reorder_point"), q.b("safety_stock"), q.b("lead_time_days")))
        r("GET", "/api/inventory", lambda q: a.inventory.stock_levels(q.user))
        r("POST", "/api/inventory/scan/preview", lambda q: a.inventory.preview_scan(q.user, q.b("scan")))
        r("POST", "/api/inventory/receive-scan", lambda q: a.inventory.receive_scan(
            q.user, q.b("scan"), q.b("quantity"), q.b("lot_number"), q.b("expiry_date"), q.b("supplier")))
        r("POST", "/api/inventory/receive", lambda q: a.inventory.receive(
            q.user, q.b("product_id"), q.b("lot_number"), q.b("expiry_date"), q.b("quantity"), q.b("supplier")))
        r("GET", "/api/inventory/alerts", lambda q: a.inventory.inventory_alerts(q.user, q.q("days", "90")))

        # Prescriptions (US3.1, US3.2)
        r("GET", "/api/prescriptions", lambda q: [
            self._rx_view(q.user, rx) for rx in a.prescriptions.list(q.user, q.q("status"), q.q("patient_id"))])
        r("POST", "/api/prescriptions", lambda q: self._rx_view(q.user, a.prescriptions.create(
            q.user, q.b("patient_id"), q.b("prescriber_id"), q.b("product_id"), q.b("quantity"),
            q.b("daily_quantity"), q.b("directions"), q.b("refills_authorized", 0), q.b("written_date"))))
        r("GET", "/api/prescriptions/{id}", lambda q: self._rx_view(q.user, a.prescriptions.get(q.user, q.params["id"])))
        r("PATCH", "/api/prescriptions/{id}", lambda q: self._rx_view(
            q.user, a.prescriptions.update_details(q.user, q.params["id"], **q.body)))
        r("POST", "/api/prescriptions/{id}/transition", lambda q: self._rx_view(
            q.user, a.prescriptions.transition(q.user, q.params["id"], q.b("to"), q.b("note"))))
        r("GET", "/api/verification-queue", lambda q: [
            {**item, "prescription": self._rx_view(q.user, item["prescription"])}
            for item in a.prescriptions.verification_queue(q.user)])

        # Clinical alerts (US4.1, US4.2)
        r("GET", "/api/alerts", lambda q: a.alerts.list_alerts(q.user, q.q("prescription_id"), q.q("status")))
        r("POST", "/api/alerts/{id}/resolve", lambda q: a.alerts.resolve(
            q.user, q.params["id"], q.b("outcome"), q.b("rationale")))
        r("GET", "/api/alerts/guidance", lambda q: a.alerts.guidance())
        r("GET", "/api/interaction-rules", lambda q: a.alerts.list_interaction_rules(q.user))

        # Purchasing (US6.1, US6.2)
        r("GET", "/api/reorder-suggestions", lambda q: a.purchasing.suggestions(q.user, q.q("all") in ("1", "true")))
        r("GET", "/api/purchase-orders", lambda q: a.purchasing.list_purchase_orders(q.user, q.q("status")))
        r("POST", "/api/purchase-orders", lambda q: a.purchasing.create_purchase_order(
            q.user, q.b("product_ids"), q.b("supplier") or "Default supplier"))
        r("POST", "/api/purchase-orders/{id}/approve", lambda q: a.purchasing.approve(
            q.user, q.params["id"], q.b("quantities"), q.b("reason")))
        r("POST", "/api/purchase-orders/{id}/reject", lambda q: a.purchasing.reject(q.user, q.params["id"], q.b("reason")))
        r("POST", "/api/purchase-orders/{id}/received", lambda q: a.purchasing.mark_received(q.user, q.params["id"]))

        # Patient self-service (US7.1, US7.2)
        r("GET", "/api/me/refillable", lambda q: a.refills.refillable_prescriptions(q.user))
        r("GET", "/api/me/refills", lambda q: a.refills.my_refills(q.user))
        r("POST", "/api/me/refills", lambda q: a.refills.get_refill(q.user, a.refills.request_refill(
            q.user, q.b("prescription_id"), q.b("pickup_branch") or BRANCHES[0], q.b("note")).id))
        r("GET", "/api/me/refills/{id}", lambda q: a.refills.get_refill(q.user, q.params["id"]))
        r("POST", "/api/me/refills/{id}/cancel", lambda q: a.refills.get_refill(
            q.user, a.refills.cancel_refill(q.user, q.params["id"]).id))
        r("GET", "/api/me/orders", lambda q: a.refills.track_orders(q.user))
        r("GET", "/api/me/notification-preferences", lambda q: {
            "preferences": a.notifications.get_preferences(q.user), "policy": a.notifications.policy()})
        r("POST", "/api/me/notification-preferences/channel", lambda q: a.notifications.set_channel(q.user, q.b("channel")))
        r("POST", "/api/me/consent", lambda q: a.notifications.give_consent(q.user, q.b("policy_version")))
        r("POST", "/api/me/consent/withdraw", lambda q: a.notifications.withdraw_consent(q.user))
        r("GET", "/api/me/notifications", lambda q: a.notifications.list_notifications(q.user))

        # Refill review (pharmacist)
        r("GET", "/api/refills/queue", lambda q: a.refills.review_queue(q.user))
        r("POST", "/api/refills/{id}/start-review", lambda q: a.refills.get_refill(
            q.user, a.refills.start_review(q.user, q.params["id"]).id))
        r("POST", "/api/refills/{id}/approve", lambda q: a.refills.get_refill(
            q.user, a.refills.approve(q.user, q.params["id"]).id))
        r("POST", "/api/refills/{id}/reject", lambda q: a.refills.get_refill(
            q.user, a.refills.reject(q.user, q.params["id"], q.b("reason")).id))

        # Sales (US8.1)
        r("GET", "/api/sales/payable", lambda q: a.sales.payable_orders(q.user))
        r("POST", "/api/sales/prescription", lambda q: a.sales.render_receipt(a.sales.record_prescription_payment(
            q.user, q.b("prescription_id"), q.b("method"), q.b("amount_tendered_cents"))))
        r("POST", "/api/sales/otc", lambda q: a.sales.render_receipt(a.sales.record_otc_sale(
            q.user, q.b("items"), q.b("method"), q.b("amount_tendered_cents"))))
        r("GET", "/api/sales", lambda q: a.sales.list_sales(q.user, q.qdate("start"), q.qdate("end")))
        r("GET", "/api/receipts/{number}", lambda q: a.sales.receipt(q.user, q.params["number"]))

        # Reports and audit (US8.2)
        r("GET", "/api/reports/sales", lambda q: a.reports.sales_report(q.user, q.qdate("start"), q.qdate("end")))
        r("GET", "/api/reports/operations", lambda q: a.reports.operations_summary(q.user))
        r("GET", "/api/reports/alert-overrides", lambda q: a.reports.alert_override_report(
            q.user, q.qdate("start"), q.qdate("end")))
        r("GET", "/api/audit", lambda q: a.audit.query(
            q.user, actor_id=q.q("actor_id"), action=q.q("action"), entity_type=q.q("entity_type"),
            entity_id=q.q("entity_id"), limit=q.qint("limit", 200)))
        r("GET", "/api/audit/verify", lambda q: a.audit.verify_chain(q.user))

    def _change_permission(self, req: Request):
        action = str(req.b("action", "")).lower()
        if action == "grant":
            return self.app.access.grant_permission(req.user, req.params["id"], req.b("permission"))
        if action == "revoke":
            return self.app.access.revoke_permission(req.user, req.params["id"], req.b("permission"))
        raise ValidationError("action must be 'grant' or 'revoke'")

    def _permission_catalog(self, req: Request):
        self.app.access.require(req.user, P.MANAGE_USERS)
        return {
            "permissions": [p.value for p in Permission],
            "roles": {role.value: sorted(p.value for p in perms) for role, perms in ROLE_DEFAULTS.items()},
            "tasks": [{"key": t.key, "label": t.label, "permission": t.permission.value} for t in DASHBOARD_TASKS],
        }
