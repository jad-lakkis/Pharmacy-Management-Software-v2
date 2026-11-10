"""Products, receiving by scan (US5.1) and stock alerts (US5.2)."""

from datetime import date
from typing import Any, Optional

from ..domain.enums import MovementReason, Permission
from ..domain.models import InventoryLot, LotAllocation, Product, StockMovement, User
from ..errors import ConflictError, NotFoundError, ValidationError
from . import barcode
from .context import Service, new_id
from .validation import optional_text, require_date, require_int, require_text

P = Permission
DEFAULT_NEAR_EXPIRY_DAYS = 90


class InventoryService(Service):
    # ---- products --------------------------------------------------------
    def add_product(
        self,
        actor: Optional[User],
        name: str,
        gtin: str,
        ingredients: list[str],
        strength: str,
        dosage_form: str,
        unit_price_cents: int,
        unit_cost_cents: int,
        requires_prescription: bool = True,
        reorder_point: int = 0,
        safety_stock: int = 0,
        lead_time_days: int = 7,
        max_daily_quantity: Optional[int] = None,
    ) -> Product:
        """``actor=None`` is only for bootstrap seeding."""
        if actor is not None:
            self.access.require(actor, P.PRODUCT_MANAGE)
        normalized = barcode.normalize_gtin(gtin)
        if self.repos.products.by_gtin(normalized):
            raise ConflictError(f"a product with GTIN {normalized} already exists")
        if not isinstance(ingredients, list) or not ingredients:
            raise ValidationError("at least one active ingredient is required")
        product = Product(
            id=new_id("PRD"),
            name=require_text(name, "name", 100),
            gtin=normalized,
            ingredients=[require_text(i, "ingredient", 60).lower() for i in ingredients],
            strength=require_text(strength, "strength", 40),
            dosage_form=require_text(dosage_form, "dosage form", 40),
            unit_price_cents=require_int(unit_price_cents, "unit price", 0),
            unit_cost_cents=require_int(unit_cost_cents, "unit cost", 0),
            requires_prescription=bool(requires_prescription),
            reorder_point=require_int(reorder_point, "reorder point", 0),
            safety_stock=require_int(safety_stock, "safety stock", 0),
            lead_time_days=require_int(lead_time_days, "lead time", 0, 365),
            max_daily_quantity=(require_int(max_daily_quantity, "max daily quantity", 1)
                                if max_daily_quantity is not None else None),
        )
        self.repos.products.add(product)
        self.audit.record(actor, "PRODUCT_CREATED", "Product", product.id, {"name": product.name, "gtin": normalized})
        return product

    def update_reorder_settings(
        self, actor: User, product_id: str, reorder_point: Any, safety_stock: Any, lead_time_days: Any
    ) -> Product:
        self.access.require(actor, P.PRODUCT_MANAGE)
        product = self.repos.products.get(product_id)
        before = [product.reorder_point, product.safety_stock, product.lead_time_days]
        product.reorder_point = require_int(reorder_point, "reorder point", 0)
        product.safety_stock = require_int(safety_stock, "safety stock", 0)
        product.lead_time_days = require_int(lead_time_days, "lead time", 0, 365)
        self.repos.products.update(product)
        self.audit.record(actor, "REORDER_SETTINGS_CHANGED", "Product", product.id,
                          {"before": before, "after": [product.reorder_point, product.safety_stock, product.lead_time_days]})
        return product

    def list_products(self, actor: User) -> list[Product]:
        self.access.require_staff(actor)
        return self.repos.products.list()

    # ---- receiving (US5.1) ----------------------------------------------
    def preview_scan(self, actor: User, raw_scan: str) -> dict[str, Any]:
        """Decode a scan and resolve its product without changing stock."""
        self.access.require(actor, P.INVENTORY_RECEIVE)
        result = barcode.parse_scan(raw_scan)
        product = self.repos.products.by_gtin(result.gtin)
        if product is None:
            raise NotFoundError(f"no product is registered for GTIN {result.gtin}")
        return {
            "gtin": result.gtin,
            "product": product,
            "lot_number": result.lot_number,
            "expiry_date": result.expiry_date,
            "needs_manual_lot": result.lot_number is None,
            "needs_manual_expiry": result.expiry_date is None,
        }

    def receive_scan(
        self,
        actor: User,
        raw_scan: str,
        quantity: Any,
        lot_number: Optional[str] = None,
        expiry_date: Any = None,
        supplier: Optional[str] = None,
    ) -> InventoryLot:
        """Receive stock from a scan. Lot/expiry typed by hand are used only
        when the barcode does not carry them; a conflicting value is refused
        so a typo cannot override what the pack says."""
        self.access.require(actor, P.INVENTORY_RECEIVE)
        result = barcode.parse_scan(raw_scan)
        product = self.repos.products.by_gtin(result.gtin)
        if product is None:
            raise NotFoundError(f"no product is registered for GTIN {result.gtin}")
        lot = result.lot_number
        if lot_number:
            manual = barcode.validate_lot(lot_number)
            if lot and manual != lot:
                raise ValidationError(f"typed lot {manual} does not match scanned lot {lot}")
            lot = lot or manual
        expiry = result.expiry_date
        if expiry_date:
            manual_expiry = require_date(expiry_date, "expiry date")
            if expiry and manual_expiry != expiry:
                raise ValidationError("typed expiry does not match the scanned expiry")
            expiry = expiry or manual_expiry
        if not lot:
            raise ValidationError("lot number is required (not in the barcode)")
        if not expiry:
            raise ValidationError("expiry date is required (not in the barcode)")
        return self.receive(actor, product.id, lot, expiry, quantity, supplier, source="scan")

    def receive(
        self,
        actor: User,
        product_id: str,
        lot_number: str,
        expiry_date: Any,
        quantity: Any,
        supplier: Optional[str] = None,
        source: str = "manual",
    ) -> InventoryLot:
        self.access.require(actor, P.INVENTORY_RECEIVE)
        product = self.repos.products.get(product_id)
        lot_number = barcode.validate_lot(require_text(lot_number, "lot number", 20))
        expiry = require_date(expiry_date, "expiry date")
        qty = require_int(quantity, "quantity", 1, 100_000)
        if expiry <= self.clock.today():
            raise ValidationError("expired stock cannot be received")
        now = self.clock.now()
        lot = self.repos.lots.by_product_and_lot(product.id, lot_number)
        if lot is not None:
            if lot.expiry_date != expiry:
                raise ValidationError(
                    f"lot {lot_number} is already recorded with expiry {lot.expiry_date.isoformat()}"
                )
            lot.quantity_received += qty
            lot.quantity_on_hand += qty
            self.repos.lots.update(lot)
        else:
            lot = InventoryLot(
                id=new_id("LOT"), product_id=product.id, lot_number=lot_number, expiry_date=expiry,
                quantity_received=qty, quantity_on_hand=qty, received_at=now, received_by=actor.id,
                supplier=optional_text(supplier, "supplier", 80),
            )
            self.repos.lots.add(lot)
        self.repos.movements.add(StockMovement(
            id=new_id("MOV"), product_id=product.id, lot_id=lot.id, delta=qty,
            reason=MovementReason.RECEIVE, reference=f"{source}:{lot_number}", at=now, by=actor.id,
        ))
        self.audit.record(actor, "STOCK_RECEIVED", "InventoryLot", lot.id, {
            "product": product.name, "lot": lot_number, "expiry": expiry.isoformat(),
            "quantity": qty, "source": source,
        })
        return lot

    # ---- stock levels ----------------------------------------------------
    def on_hand(self, product_id: str, today: Optional[date] = None) -> int:
        """Sellable quantity: non-expired lots only."""
        today = today or self.clock.today()
        return sum(l.quantity_on_hand for l in self.repos.lots.for_product(product_id) if l.expiry_date > today)

    def stock_levels(self, actor: User) -> list[dict[str, Any]]:
        self.access.require(actor, P.INVENTORY_VIEW)
        today = self.clock.today()
        rows = []
        for product in self.repos.products.list():
            lots = sorted(self.repos.lots.for_product(product.id), key=lambda l: l.expiry_date)
            sellable = sum(l.quantity_on_hand for l in lots if l.expiry_date > today)
            expired = sum(l.quantity_on_hand for l in lots if l.expiry_date <= today)
            rows.append({
                "product": product,
                "on_hand": sellable,
                "expired_on_shelf": expired,
                "reorder_point": product.reorder_point,
                "below_reorder_point": sellable <= product.reorder_point,
                "lots": [l for l in lots if l.quantity_on_hand > 0],
            })
        return rows

    def allocate(self, actor: User, product_id: str, quantity: int, reason: MovementReason, reference: str) -> list[LotAllocation]:
        """Deduct stock first-expiry-first-out from non-expired lots.

        Internal: callers (dispensing, OTC sales) check their own permission.
        Raises before writing anything if stock is insufficient.
        """
        today = self.clock.today()
        lots = sorted(
            (l for l in self.repos.lots.for_product(product_id) if l.expiry_date > today and l.quantity_on_hand > 0),
            key=lambda l: (l.expiry_date, l.received_at),
        )
        available = sum(l.quantity_on_hand for l in lots)
        if available < quantity:
            product = self.repos.products.get(product_id)
            raise ConflictError(f"insufficient stock of {product.name}: {available} available, {quantity} needed")
        remaining = quantity
        allocations: list[LotAllocation] = []
        now = self.clock.now()
        for lot in lots:
            if remaining == 0:
                break
            take = min(remaining, lot.quantity_on_hand)
            lot.quantity_on_hand -= take
            remaining -= take
            self.repos.lots.update(lot)
            self.repos.movements.add(StockMovement(
                id=new_id("MOV"), product_id=product_id, lot_id=lot.id, delta=-take,
                reason=reason, reference=reference, at=now, by=actor.id,
            ))
            allocations.append(LotAllocation(lot.id, lot.lot_number, lot.expiry_date, take))
        return allocations

    # ---- alerts (US5.2) --------------------------------------------------
    def inventory_alerts(self, actor: User, near_expiry_days: Any = DEFAULT_NEAR_EXPIRY_DAYS) -> list[dict[str, Any]]:
        self.access.require(actor, P.INVENTORY_ALERTS)
        window = require_int(near_expiry_days, "near-expiry window", 1, 730)
        today = self.clock.today()
        alerts: list[dict[str, Any]] = []
        for product in self.repos.products.list():
            lots = self.repos.lots.for_product(product.id)
            on_hand = sum(l.quantity_on_hand for l in lots if l.expiry_date > today)
            if on_hand == 0:
                alerts.append({
                    "type": "OUT_OF_STOCK", "severity": "HIGH", "product_id": product.id,
                    "product": product.name, "on_hand": 0, "reorder_point": product.reorder_point,
                    "message": f"{product.name} is out of stock",
                })
            elif on_hand <= product.reorder_point:
                alerts.append({
                    "type": "LOW_STOCK", "severity": "MODERATE", "product_id": product.id,
                    "product": product.name, "on_hand": on_hand, "reorder_point": product.reorder_point,
                    "message": f"{product.name}: {on_hand} on hand, at or below reorder point {product.reorder_point}",
                })
            for lot in lots:
                if lot.quantity_on_hand <= 0:
                    continue
                days_left = (lot.expiry_date - today).days
                base = {
                    "product_id": product.id, "product": product.name, "lot_id": lot.id,
                    "lot_number": lot.lot_number, "expiry_date": lot.expiry_date.isoformat(),
                    "quantity": lot.quantity_on_hand, "days_left": days_left,
                }
                if days_left <= 0:
                    alerts.append({**base, "type": "EXPIRED", "severity": "HIGH",
                                   "message": f"Lot {lot.lot_number} of {product.name} has expired; remove {lot.quantity_on_hand} units"})
                elif days_left <= window:
                    alerts.append({**base, "type": "NEAR_EXPIRY", "severity": "MODERATE" if days_left <= 30 else "LOW",
                                   "message": f"Lot {lot.lot_number} of {product.name} expires in {days_left} days"})
        order = {"HIGH": 0, "MODERATE": 1, "LOW": 2}
        alerts.sort(key=lambda a: (order[a["severity"]], a["product"]))
        return alerts
