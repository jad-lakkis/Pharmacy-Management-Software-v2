# Sprint 1 — Pharmacy Management Software

Source backlog: Jira project **USER** ("Pharmacy Management Software").
UI reference: Figma file **Prototype-1**, page "assignment 3 extended prototype".

## Prioritisation rule

Stories are prioritised by their Jira **story point rating** (highest first).
Every issue is "Medium" priority in Jira, so story points are the only
differentiating rating. Ties are broken by Jira key order.

Two adjustments, both recorded here so the ordering can be audited:

1. **Technical foundations first.** US1.1 (roles and permissions) is only
   1 point, but every other story depends on authorisation, so it is built as
   part of the foundation layer (repositories, audit log, RBAC) before the
   ranked stories. It keeps its 1-point rank in the table.
2. **USER-13 (8 pts) was split** in Jira into USER-20 (track, 5 pts) and
   USER-21 (request, 1 pt). The sprint delivers the two children; USER-13 is
   treated as their umbrella, so its points are not counted twice.

## Sprint backlog (ranked)

| Rank | Jira | Story | Pts | Where it lives |
|---|---|---|---|---|
| 1 | USER-13 → 20, 21 | US7.1 Patient requests a refill and tracks its status | 8 (5+1) | `services/refills.py` |
| 2 | USER-5 | US3.1 Prescription moves through defined workflow stages | 5 | `services/prescriptions.py` |
| 3 | USER-6 | US3.2 Dispensing requires pharmacist verification | 5 | `services/prescriptions.py` |
| 4 | USER-8 | US4.2 Record how a clinical alert was resolved (auditable) | 5 | `services/clinical_alerts.py` |
| 5 | USER-16 | US8.2 Reports and timestamped audit logs | 5 | `services/reports.py`, `services/audit.py` |
| 6 | USER-20 | US7.1a Patient tracks orders | 5 | `services/refills.py` (`track_orders`) |
| 7 | USER-23 | Task: order tracking system | 5 | `services/refills.py` + status listener |
| 8 | USER-3 | US2.1 Create/update patient and prescriber profiles | 3 | `services/patients.py` |
| 9 | USER-7 | US4.1 Alerts show triggering data and reference basis | 3 | `services/clinical_alerts.py` |
| 10 | USER-9 | US5.1 Scan and record lot and expiry on receiving | 3 | `services/inventory.py`, `services/barcode.py` |
| 11 | USER-10 | US5.2 Low-stock and near-expiry alerts | 3 | `services/inventory.py` |
| 12 | USER-11 | US6.1 Reorder suggestions show their calculation values | 3 | `services/purchasing.py` |
| 13 | USER-14 | US7.2 Notification channel and consent | 3 | `services/notifications.py` |
| 14 | USER-22 | Task: lot scanner | 3 | `services/barcode.py` |
| 15 | USER-12 | US6.2 Approve / modify / reject a suggested PO | 2 | `services/purchasing.py` |
| 16 | USER-17 | US1.2 Dashboard shows only permitted tasks | 2 | `services/access.py` (`dashboard`) |
| 17 | USER-24 | Task: user classes for consent/ownership | 2 | `domain/models.py` (`User.patient_id`), ownership checks |
| 18 | USER-1 | US1.1 Owner assigns roles and permissions | 1 | `services/access.py` |
| 19 | USER-4 | US2.2 Pharmacist views allergies and medication history | 1 | `services/patients.py` (`clinical_profile`) |
| 20 | USER-15 | US8.1 Cashier records payments and issues receipts | 1 | `services/sales.py` |
| 21 | USER-21 | US7.1b Patient requests refills | 1 | `services/refills.py` |

Total delivered: 61 story points (USER-13 counted through its children).

## Acceptance criteria implemented

Jira issues have no descriptions, so the criteria below were derived from the
story text and the Figma prototype. They are what the code enforces.

**US1.1 Roles and permissions (USER-1)**
- Six roles with least-privilege defaults: Owner, Manager, Pharmacist, Technician, Cashier, Patient.
- Owner can add members, change roles, grant/revoke individual permissions, reset passwords and deactivate ("Delete Users") or reactivate accounts.
- Deactivation is a soft delete so the audit trail keeps its actor references.
- The system refuses changes that would leave no active user able to manage access, and an owner cannot deactivate themself.
- Patient-only permissions cannot be granted to staff and vice versa.

**US1.2 Permitted dashboard (USER-17)**
- The dashboard lists only tasks the user's effective permissions allow; labels follow the Figma home screens.
- Every service call re-checks permissions server-side; hiding a tile is not the security boundary.

**US2.1 Patient and prescriber profiles (USER-3)**
- Create/update patients (name, date of birth, phone, email, address) and allergies; create/update prescribers with a unique licence number.
- Validation: required names, date of birth not in the future, basic email/phone format, duplicate patient (same name and DOB) rejected.

**US2.2 Allergies and medication history (USER-4)**
- Pharmacist-only clinical profile: allergies with severity/reaction and full prescription history with status, prescriber and dispense date.
- Each view of a clinical profile is written to the audit log.

**US3.1 Workflow stages (USER-5)**
- Stages: Received → Data entered → Awaiting verification → Verified → Dispensed → Picked up, plus On hold (resumes to the stage it left) and Cancelled.
- Only transitions in the table are allowed; each needs a specific permission; every change is timestamped in the prescription's status history and the audit log.
- Returning a prescription for correction and cancelling require a note.

**US3.2 Pharmacist verification before dispensing (USER-6)**
- Only users with the verify permission can move a prescription to Verified.
- Verification is refused while any clinical alert is open, or if an alert was resolved as "discontinue" or "prescriber changed therapy".
- Dispensing is only possible from Verified; it deducts stock first-expiry-first-out from non-expired lots and fails on insufficient stock.
- Picked up is only possible after a payment is recorded.

**US4.1 Alert evidence (USER-7)**
- Screening runs when a prescription is submitted for verification: allergy match, drug–drug interaction, duplicate therapy, daily-dose limit.
- Each alert stores the triggering data (key/value) and its reference basis (allergy record, interaction reference source, labelling limit, policy).

**US4.2 Alert resolution (USER-8)**
- Resolution requires an outcome and a written rationale (at least 10 characters); records who and when; written to the audit log.
- Resubmitting a prescription supersedes old open alerts and re-screens; resolved alerts are kept.

**US5.1 Lot and expiry receiving (USER-9, USER-22)**
- Accepts GS1 scans (`(01)GTIN(17)YYMMDD(10)LOT`, or raw with FNC1/GS separators), validates the GTIN check digit and resolves the product.
- Plain GTIN/EAN scans are accepted with lot and expiry entered manually.
- Expired goods are refused; re-receiving an existing lot must match its expiry.

**US5.2 Stock alerts (USER-10)**
- Low stock (on hand ≤ reorder point), out of stock, near expiry (default 90 days, configurable) and expired-on-shelf alerts, each with the values that triggered it.

**US6.1 Explained reorder suggestions (USER-11)**
- Suggestion appears when on hand + on order ≤ reorder point.
- `suggested = ceil(avg daily usage × (lead time + review period) + safety stock − (on hand + on order))`, minimum 1.
- Every input (usage window, units used, average, lead time, review period, safety stock, on hand, on order, target) is returned and shown.

**US6.2 Human decision on POs (USER-12)**
- Generated POs start as Pending approval; nothing is ever auto-approved.
- Manager/owner approves, modifies quantities (reason required) or rejects (reason required); the original suggested quantity is kept on every line.

**US7.1 Refills and tracking (USER-13, USER-20, USER-21, USER-23)**
- Patient sees only their own prescriptions with refills remaining, picks a pickup branch and submits (request numbers `RF-001`, …).
- Refused when no refills remain, the prescription is older than a year, it was never filled, or a request is already open.
- Pharmacist reviews, approves (creates a new fill that goes through the normal workflow) or rejects with a reason.
- Request status follows the fill automatically: Ready for pickup when dispensed, Picked up when paid; a cancelled fill returns the refill.
- Patients can cancel a request while it is still Submitted, and see a timestamped timeline of every request and order.

**US7.2 Notification channel and consent (USER-14)**
- Channels: SMS, email, in-app. SMS/email require the matching contact detail on file.
- Consent must reference the current policy version; giving, withdrawing and changing channel are kept as a history.
- Notifications are only sent with consent; otherwise they are recorded as suppressed with the reason.

**US8.1 Payments and receipts (USER-15)**
- Cashier sees dispensed, unpaid prescriptions; records cash (change calculated) or card (exact amount) payments.
- Each sale is linked to its prescription (or over-the-counter lines), gets a unique receipt number, and a prescription cannot be paid twice.

**US8.2 Reports and audit logs (USER-16)**
- Sales report by date range (totals, by method, by day, top products), operations summary, alert-override report.
- Append-only, hash-chained audit log with timestamp, actor, action, entity and details; filterable; chain integrity check endpoint.

## Deviations and notes for the auditor

- **Manager role.** Jira assigns purchasing and stock alerts to a manager, which the prototype lacks; the role is added and shares those tasks with the owner.
- **Owner is not clinical.** Owner defaults exclude patient clinical data (least privilege); owner can grant it explicitly.
- **"Delete Users"** is implemented as deactivation, not deletion, to preserve audit integrity.
- **Unscoped Figma buttons.** Cashier "Recurrent prescriptions" maps to the list of dispensed prescriptions awaiting payment; "Info: Making a form" maps to the alert-resolution guidance text.
- **Out of scope:** real SMS/email delivery (simulated outbox), payment terminal integration, multi-branch stock, refunds/voids.
