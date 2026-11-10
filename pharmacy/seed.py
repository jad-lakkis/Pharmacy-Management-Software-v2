"""Demo data, created through the real services so that every record,
stock movement and audit entry is consistent.

The seed replays about 40 days of pharmacy activity on a fixed clock that
ends just before "now", leaving work waiting in every queue: prescriptions
to verify (with open alerts), dispensed orders to pay, low and near-expiry
stock, a reorder suggestion, and a patient with refills available.

The interaction rules below are DEMO DATA for exercising the software.
They are not a clinical reference.
"""

from datetime import datetime, timedelta
from typing import Optional

from .clock import FixedClock, SystemClock
from .domain.enums import AlertSeverity, PaymentMethod, PrescriptionStatus, Role
from .domain.models import InteractionRule
from .repositories import Repositories
from .services import PharmacyApp
from .services.barcode import gtin_check_digit
from .services.context import new_id

S = PrescriptionStatus

# Local demo credentials only. Change or remove before any real deployment.
DEMO_ACCOUNTS = {
    "owner": ("owner-demo-1", Role.OWNER, "Owner"),
    "manager": ("manager-demo-1", Role.MANAGER, "Manager Sami"),
    "leila": ("pharmacist-demo-1", Role.PHARMACIST, "Pharmacist Leila"),
    "karim": ("technician-demo-1", Role.TECHNICIAN, "Technician Karim"),
    "cashier": ("cashier-demo-1", Role.CASHIER, "Cashier Nour"),
    "maya": ("patient-demo-1", Role.PATIENT, "Patient Maya"),
}


def demo_gtin(serial: int) -> str:
    """A valid GTIN-13 in the 200-299 'restricted circulation' prefix range,
    which is reserved for internal use, so it never collides with real packs."""
    body = f"20000000{serial:04d}"
    return body + str(gtin_check_digit(body))


PRODUCTS = [
    # key, name, ingredients, strength, form, unit price (cents), unit cost (cents),
    # prescription only?, reorder point, safety stock, lead time (days), max units/day
    ("lisinopril", "Lisinopril", ["lisinopril"], "10 mg", "tablet", 45, 20, True, 60, 30, 5, 4),
    ("atorvastatin", "Atorvastatin", ["atorvastatin"], "20 mg", "tablet", 60, 25, True, 40, 20, 5, 4),
    ("amoxicillin", "Amoxicillin", ["amoxicillin"], "500 mg", "capsule", 35, 12, True, 30, 15, 3, 6),
    ("warfarin", "Warfarin", ["warfarin"], "5 mg", "tablet", 30, 10, True, 20, 10, 7, 2),
    ("aspirin", "Aspirin", ["aspirin"], "81 mg", "tablet", 10, 3, False, 50, 20, 3, 4),
    ("paracetamol", "Paracetamol", ["paracetamol"], "500 mg", "tablet", 8, 2, False, 100, 40, 3, 8),
    ("spironolactone", "Spironolactone", ["spironolactone"], "25 mg", "tablet", 55, 22, True, 25, 10, 10, 4),
    ("clarithromycin", "Clarithromycin", ["clarithromycin"], "500 mg", "tablet", 120, 50, True, 20, 10, 5, 2),
    ("simvastatin", "Simvastatin", ["simvastatin"], "20 mg", "tablet", 40, 15, True, 30, 15, 5, 2),
    ("metformin", "Metformin", ["metformin"], "500 mg", "tablet", 20, 7, True, 80, 40, 5, 6),
]

INTERACTIONS = [
    ("warfarin", "aspirin", AlertSeverity.HIGH, "increased bleeding risk"),
    ("lisinopril", "spironolactone", AlertSeverity.MODERATE, "risk of high potassium (hyperkalaemia)"),
    ("simvastatin", "clarithromycin", AlertSeverity.HIGH, "raised statin levels; risk of muscle damage"),
    ("atorvastatin", "clarithromycin", AlertSeverity.MODERATE, "raised statin levels"),
]


def seed(repos: Repositories, now: Optional[datetime] = None) -> None:
    if not repos.is_empty():
        return
    real_now = now or SystemClock().now()
    clock = FixedClock(real_now - timedelta(days=40))
    app = PharmacyApp(repos, clock)
    with repos.transaction():
        _seed(app, clock, real_now)


def _seed(app: PharmacyApp, clock: FixedClock, real_now: datetime) -> None:
    repos = app.repos

    # --- people ---------------------------------------------------------
    pw, role, name = DEMO_ACCOUNTS["owner"]
    owner = app.access.create_user(None, "owner", name, role, pw)
    staff = {"owner": owner}
    for username in ("manager", "leila", "karim", "cashier"):
        pw, role, name = DEMO_ACCOUNTS[username]
        staff[username] = app.access.create_user(owner, username, name, role, pw)
    leila, karim, cashier = staff["leila"], staff["karim"], staff["cashier"]

    dr_saleh = app.patients.create_prescriber(karim, "Dr. Hala Saleh", "LB-MD-10422", "+961 1 350 000", "Cardiology", "Beirut Heart Clinic")
    dr_nasr = app.patients.create_prescriber(karim, "Dr. Fadi Nasr", "LB-MD-20857", "+961 1 744 100", "Family medicine", "Hamra Family Practice")

    maya = app.patients.create_patient(karim, "Maya", "Haddad", "1988-04-12", "+961 70 123 456", "maya.haddad@example.com", "Hamra, Beirut")
    omar = app.patients.create_patient(karim, "Omar", "Khoury", "1975-09-30", "+961 71 222 333", None, "Achrafieh, Beirut")
    rania = app.patients.create_patient(karim, "Rania", "Saad", "1952-01-18", "+961 3 444 555", "rania.saad@example.com", None)
    app.patients.add_allergy(karim, omar.id, "amoxicillin", "hives and facial swelling", "SEVERE")
    app.patients.add_allergy(karim, maya.id, "sulfa", "rash", "MILD")

    pw, role, name = DEMO_ACCOUNTS["maya"]
    maya_user = app.access.create_user(owner, "maya", name, role, pw, patient_id=maya.id)

    # --- catalogue and reference data -----------------------------------
    products = {}
    for i, (key, pname, ingr, strength, form, price, cost, rx_only, rop, ss, lead, maxd) in enumerate(PRODUCTS, start=1):
        products[key] = app.inventory.add_product(
            owner, pname, demo_gtin(i), ingr, strength, form, price, cost,
            rx_only, rop, ss, lead, maxd,
        )
    for a, b, severity, description in INTERACTIONS:
        repos.interaction_rules.add(InteractionRule(
            id=new_id("IXR"), ingredient_a=a, ingredient_b=b, severity=severity, description=description,
            reference_source=f"Demo interaction table (seed data, not clinical advice): {a} + {b}",
        ))
    app.audit.record(owner, "INTERACTION_RULES_LOADED", "InteractionRule", None, {"count": len(INTERACTIONS)})

    # --- stock received 40 days ago ---------------------------------------
    d = clock.today()
    receive = [
        ("lisinopril", "LIS2401", 400, 260), ("atorvastatin", "ATV2402", 400, 160),
        ("amoxicillin", "AMX2403", 365, 100), ("amoxicillin", "AMX2399", 20, 15),
        ("warfarin", "WAR2404", 500, 60), ("aspirin", "ASP2405", 85, 120),
        ("paracetamol", "PAR2406", 600, 300), ("spironolactone", "SPI2407", 300, 40),
        ("clarithromycin", "CLA2408", 200, 40), ("simvastatin", "SIM2409", 300, 80),
        ("metformin", "MET2410", 500, 400),
    ]
    for key, lot, days_to_expiry, qty in receive:
        app.inventory.receive(karim, products[key].id, lot, d + timedelta(days=days_to_expiry), qty, "MedSupply SARL")

    # --- 35 days of completed prescriptions (gives reorder usage) ------
    def complete(patient, prescriber, key, qty, daily, directions, refills=0, pay=PaymentMethod.CARD):
        rx = app.prescriptions.create(karim, patient.id, prescriber.id, products[key].id, qty, daily, directions, refills)
        app.prescriptions.transition(karim, rx.id, S.DATA_ENTERED)
        app.prescriptions.transition(karim, rx.id, S.AWAITING_VERIFICATION)
        for alert in app.alerts.list_alerts(leila, rx.id, "OPEN"):
            app.alerts.resolve(leila, alert.id, "PRESCRIBER_CONFIRMED", "Discussed with prescriber, therapy confirmed as written.")
        app.prescriptions.transition(leila, rx.id, S.VERIFIED)
        app.prescriptions.transition(karim, rx.id, S.DISPENSED)
        total = qty * products[key].unit_price_cents
        app.sales.record_prescription_payment(cashier, rx.id, pay, total if pay == PaymentMethod.CARD else total + 500)
        return rx

    clock.advance(days=5, hours=3)
    complete(omar, dr_nasr, "metformin", 60, 2, "Take 1 tablet twice daily with meals", 5)
    clock.advance(days=2)
    complete(rania, dr_saleh, "lisinopril", 30, 1, "Take 1 tablet once daily", 2, PaymentMethod.CASH)
    clock.advance(days=3)
    complete(omar, dr_nasr, "simvastatin", 60, 1, "Take 1 tablet at night")
    clock.advance(days=4)
    complete(rania, dr_saleh, "lisinopril", 60, 1, "Take 1 tablet once daily", 0, PaymentMethod.CASH)
    clock.advance(days=3)
    complete(omar, dr_nasr, "lisinopril", 90, 1, "Take 1 tablet once daily")
    clock.advance(days=2)
    # Maya's two medicines with refills (Figma "Medication A" and "B").
    complete(maya, dr_saleh, "lisinopril", 30, 1, "Take 1 tablet every morning", 1)
    complete(maya, dr_saleh, "atorvastatin", 30, 1, "Take 1 tablet at night", 2)
    clock.advance(days=4)
    complete(rania, dr_saleh, "warfarin", 30, 1, "Take 1 tablet daily at 6 pm; INR check weekly", 2)
    clock.advance(days=3)
    complete(omar, dr_nasr, "spironolactone", 30, 1, "Take 1 tablet every morning")
    sale = app.sales.record_otc_sale(cashier, [{"product_id": products["paracetamol"].id, "quantity": 20}], "CASH", 2000)
    del sale

    # --- work in progress, a few hours before now --------------------------
    clock.set(real_now - timedelta(hours=6))
    # Dispensed and waiting for payment (cashier queue)
    rx = app.prescriptions.create(karim, omar.id, dr_nasr.id, products["metformin"].id, 60, 2, "Take 1 tablet twice daily with meals", 3)
    app.prescriptions.transition(karim, rx.id, S.DATA_ENTERED)
    app.prescriptions.transition(karim, rx.id, S.AWAITING_VERIFICATION)
    app.prescriptions.transition(leila, rx.id, S.VERIFIED)
    app.prescriptions.transition(karim, rx.id, S.DISPENSED)

    clock.advance(hours=1)
    # Interaction alert waiting for the pharmacist: warfarin is active for Rania
    rx = app.prescriptions.create(karim, rania.id, dr_saleh.id, products["aspirin"].id, 30, 1, "Take 1 tablet daily with food")
    app.prescriptions.transition(karim, rx.id, S.DATA_ENTERED)
    app.prescriptions.transition(karim, rx.id, S.AWAITING_VERIFICATION)

    clock.advance(hours=1)
    # Allergy alert (severe) waiting for the pharmacist
    rx = app.prescriptions.create(karim, omar.id, dr_nasr.id, products["amoxicillin"].id, 21, 3, "Take 1 capsule three times daily for 7 days")
    app.prescriptions.transition(karim, rx.id, S.DATA_ENTERED)
    app.prescriptions.transition(karim, rx.id, S.AWAITING_VERIFICATION)

    clock.advance(hours=1)
    # Interaction + dose alerts: clarithromycin with simvastatin still active
    rx = app.prescriptions.create(karim, omar.id, dr_nasr.id, products["clarithromycin"].id, 14, 3, "Take 1 tablet three times daily")
    app.prescriptions.transition(karim, rx.id, S.DATA_ENTERED)
    app.prescriptions.transition(karim, rx.id, S.AWAITING_VERIFICATION)

    clock.advance(hours=1)
    # Just received at the counter, waiting for data entry
    app.prescriptions.create(karim, maya.id, dr_nasr.id, products["amoxicillin"].id, 21, 3, "Take 1 capsule three times daily for 7 days")

    # Maya chose SMS and consented
    app.notifications.set_channel(maya_user, "SMS")
    app.notifications.give_consent(maya_user, app.notifications.policy()["version"])
