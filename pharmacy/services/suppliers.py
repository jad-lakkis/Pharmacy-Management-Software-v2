"""Supplier directory: contact details and per-product price lists.

Used by purchasing: a purchase order drafted for a listed supplier takes its
unit costs from that supplier's price list. Permissions are the existing
purchasing ones (view = ``purchasing.view``, change = ``purchasing.decide``),
so Owner and Manager can use it and nobody else gains access.
"""

from typing import Any, Optional

from ..domain.enums import Permission
from ..domain.models import Supplier, SupplierPrice, User
from ..errors import ConflictError, ValidationError
from .context import Service, new_id
from .validation import optional_email, optional_phone, optional_text, require_int, require_text

P = Permission

SUPPLIER_EDITABLE = ("name", "contact_name", "phone", "email", "address")
MAX_UNIT_COST_CENTS = 10_000_000


class SupplierService(Service):
    # ---- reads ---------------------------------------------------------------
    def list_suppliers(self, actor: User, include_inactive: bool = False) -> list[Supplier]:
        self.access.require(actor, P.PURCHASE_VIEW)
        suppliers = self.repos.suppliers.list()
        if not include_inactive:
            suppliers = [s for s in suppliers if s.active]
        return sorted(suppliers, key=lambda s: s.name.lower())

    def get_supplier(self, actor: User, supplier_id: str) -> Supplier:
        self.access.require(actor, P.PURCHASE_VIEW)
        return self.repos.suppliers.get(supplier_id)

    # ---- profile -------------------------------------------------------------
    def create_supplier(
        self,
        actor: User,
        name: Any,
        contact_name: Any = None,
        phone: Any = None,
        email: Any = None,
        address: Any = None,
    ) -> Supplier:
        self.access.require(actor, P.PURCHASE_DECIDE)
        fields = self._clean_fields(
            {"name": name, "contact_name": contact_name, "phone": phone, "email": email, "address": address}
        )
        if self.repos.suppliers.by_name(fields["name"]):
            raise ConflictError(f"supplier '{fields['name']}' already exists")
        now = self.clock.now()
        supplier = Supplier(id=new_id("SUP"), created_at=now, updated_at=now, **fields)
        self.repos.suppliers.add(supplier)
        self.audit.record(actor, "SUPPLIER_CREATED", "Supplier", supplier.id, {"name": supplier.name})
        return supplier

    def update_supplier(self, actor: User, supplier_id: str, **changes: Any) -> Supplier:
        self.access.require(actor, P.PURCHASE_DECIDE)
        unknown = set(changes) - set(SUPPLIER_EDITABLE)
        if unknown:
            raise ValidationError(f"cannot update: {', '.join(sorted(unknown))}")
        supplier = self.repos.suppliers.get(supplier_id)
        merged = {k: getattr(supplier, k) for k in SUPPLIER_EDITABLE}
        merged.update(changes)
        fields = self._clean_fields(merged)
        other = self.repos.suppliers.by_name(fields["name"])
        if other and other.id != supplier.id:
            raise ConflictError(f"supplier '{fields['name']}' already exists")
        changed = {k: [str(getattr(supplier, k)), str(v)] for k, v in fields.items() if getattr(supplier, k) != v}
        for key, value in fields.items():
            setattr(supplier, key, value)
        supplier.updated_at = self.clock.now()
        self.repos.suppliers.update(supplier)
        self.audit.record(actor, "SUPPLIER_UPDATED", "Supplier", supplier.id, {"changed": changed})
        return supplier

    def deactivate(self, actor: User, supplier_id: str) -> Supplier:
        return self._set_active(actor, supplier_id, False)

    def reactivate(self, actor: User, supplier_id: str) -> Supplier:
        return self._set_active(actor, supplier_id, True)

    def _set_active(self, actor: User, supplier_id: str, active: bool) -> Supplier:
        self.access.require(actor, P.PURCHASE_DECIDE)
        supplier = self.repos.suppliers.get(supplier_id)
        if supplier.active == active:
            return supplier
        supplier.active = active
        supplier.updated_at = self.clock.now()
        self.repos.suppliers.update(supplier)
        self.audit.record(actor, "SUPPLIER_REACTIVATED" if active else "SUPPLIER_DEACTIVATED",
                          "Supplier", supplier.id, {"name": supplier.name})
        return supplier

    # ---- price list ----------------------------------------------------------
    def set_price(self, actor: User, supplier_id: str, product_id: Any, unit_cost_cents: Any) -> Supplier:
        """Add or change what this supplier charges per unit of a product."""
        self.access.require(actor, P.PURCHASE_DECIDE)
        supplier = self.repos.suppliers.get(supplier_id)
        product = self.repos.products.get(require_text(product_id, "product", 80))
        cost = require_int(unit_cost_cents, "unit cost (cents)", 1, MAX_UNIT_COST_CENTS)
        now = self.clock.now()
        existing = next((p for p in supplier.prices if p.product_id == product.id), None)
        previous = existing.unit_cost_cents if existing else None
        if existing:
            existing.unit_cost_cents, existing.updated_at = cost, now
        else:
            supplier.prices.append(SupplierPrice(product.id, product.name, cost, now))
        supplier.updated_at = now
        self.repos.suppliers.update(supplier)
        self.audit.record(actor, "SUPPLIER_PRICE_SET", "Supplier", supplier.id,
                          {"product": product.name, "from_cents": previous, "to_cents": cost})
        return supplier

    def remove_price(self, actor: User, supplier_id: str, product_id: str) -> Supplier:
        self.access.require(actor, P.PURCHASE_DECIDE)
        supplier = self.repos.suppliers.get(supplier_id)
        existing = next((p for p in supplier.prices if p.product_id == product_id), None)
        if existing is None:
            raise ValidationError("this supplier has no price for that product")
        supplier.prices = [p for p in supplier.prices if p.product_id != product_id]
        supplier.updated_at = self.clock.now()
        self.repos.suppliers.update(supplier)
        self.audit.record(actor, "SUPPLIER_PRICE_REMOVED", "Supplier", supplier.id,
                          {"product": existing.product_name, "was_cents": existing.unit_cost_cents})
        return supplier

    # ---- helpers -------------------------------------------------------------
    @staticmethod
    def price_for(supplier: Supplier, product_id: str) -> Optional[int]:
        for price in supplier.prices:
            if price.product_id == product_id:
                return price.unit_cost_cents
        return None

    @staticmethod
    def _clean_fields(raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": require_text(raw.get("name"), "supplier name", 80),
            "contact_name": optional_text(raw.get("contact_name"), "contact name", 80),
            "phone": optional_phone(raw.get("phone")),
            "email": optional_email(raw.get("email")),
            "address": optional_text(raw.get("address"), "address", 200),
        }
