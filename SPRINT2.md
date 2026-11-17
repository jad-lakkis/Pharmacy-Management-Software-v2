# Sprint 2 — Pharmacy Management Software

Second potentially shippable increment, built on top of [Sprint 1](SPRINT.md).
Sprint 2 only **adds** to the product: every Sprint 1 screen, rule and
permission behaves exactly as before, and databases created in Sprint 1 still
open and work.

Source backlog: Jira project **USER** ("Pharmacy Management Software").
UI reference: Figma file **Prototype-1**.

## Scrum roles

| Role | Person | Responsibility in this project |
|---|---|---|
| Product Owner | Ibrahim Al Sakka | Owns the product backlog and its order of priority; decides what counts as done for each story; accepts or rejects the increment at the sprint review. |
| Scrum Master | Karl Moussawmai | Makes sure the team follows Scrum; removes blockers; keeps the sprint backlog and velocity up to date; reports the weekly progress on Moodle; prepares the lab presentation. |
| Developer | Jad Lakkis | Designs, builds and tests the sprint backlog items; keeps the GitHub repository up to date so the instructor can follow progress; demonstrates the working software at the sprint review. |

## How Scrum is applied

| Scrum element | In this project |
|---|---|
| Product backlog | Jira project **USER**, plus the gaps found when the Sprint 1 product was run and reviewed (listed at the end of this file). |
| Sprint planning | Stories are selected from the product backlog, sized in story points, and ranked by the same rule as Sprint 1 (highest points first). |
| Sprint backlog | The table "Sprint 2 backlog" below. |
| Sprint | Build the selected stories; each one is finished completely or counts as zero. |
| Increment | The running application plus its code on GitHub, usable on a clean database (`python -m pharmacy --reset`). |
| Sprint review | Demo script below; the Product Owner decides whether each story is accepted. |
| Retrospective | "What to improve" below. |
| Weekly report | Scrum Master, on Moodle. |

**Story point scale:** 1, 2, 3, 5, 8 — the same relative scale as Sprint 1
(for example "Approve/modify/reject a PO" = 2, "Scan and record lot and
expiry" = 3, "Prescription workflow stages" = 5).

## Sprint 2 goal

Make the pharmacy's supplier and stock data manageable from the application:
keep supplier contact details and prices, and let staff add a medicine that is
not in the catalogue yet and then receive stock for it.

## Sprint 2 backlog (ranked)

| Rank | Id | Story | Pts | Where it lives |
|---|---|---|---|---|
| 1 | S2-1 | As an owner or manager, I keep a **supplier directory** with contact details and a price list, and draft purchase orders for a listed supplier. | 5 | `services/suppliers.py`, `services/purchasing.py`, `api/routes.py`, `web/app.js`, `seed.py` |
| 2 | S2-2 | As an owner or manager, I **add a new medicine** to the catalogue so staff can receive stock for it. | 2 | `web/app.js`, `services/access.py` (dashboard task) |
| 3 | S2-3 | Task: the application starts on **Python 3.11 and 3.14**. | 1 | `repositories/base.py`, `services/prescriptions.py` |

Sprint 2 total: **8 story points**, all done.

## Acceptance criteria implemented

**S2-1 Supplier directory**
- Owners and managers can see the **Suppliers** task; the other roles have no tile and the server refuses their requests.
- A supplier has a name (unique, ignoring upper/lower case), contact person, phone, email (format checked) and address; it can be edited, deactivated and reactivated.
- A price list holds the price per unit that the supplier charges for a product (must be above zero). Setting a price again updates it, so a product appears only once per supplier; a price can be removed.
- A deactivated supplier is hidden by default and cannot be used for new purchase orders; existing orders keep their supplier name.
- On the reorder screen the user can pick a supplier. Lines for products on that supplier's price list use its price; other lines keep the product's default cost. Each line shows where its unit cost came from ("price list" or "default cost").
- If no supplier is picked, a purchase order is drafted exactly as in Sprint 1 ("Default supplier", product default cost).
- Purchase orders are still never approved automatically.
- Every change is written to the audit log (`SUPPLIER_*` actions); the audit chain still verifies.
- Two demo suppliers with price lists are added to the demo data.

**S2-2 Add a new medicine**
- Owners and managers (the product-management permission) get a **Products** task with the product catalogue: name, strength and form, barcode, price, cost, whether a prescription is needed, reorder point.
- The add form takes name, barcode (GTIN of 8, 12, 13 or 14 digits), active ingredients, strength, dosage form, selling price, cost, reorder point, safety stock, supplier lead time, an optional maximum number of units per day, and whether a prescription is required.
- The barcode check digit is validated and a barcode that already exists is refused; at least one active ingredient is required.
- After adding, the technician can receive stock for the product by scanning its barcode (before this, an unknown barcode was rejected). The product also appears in inventory (below its reorder point until stock arrives), in reorder suggestions, in prescription entry, and in over-the-counter sales when it does not need a prescription.
- Staff without the product-management permission have no Products tile and the server refuses their requests.
- Adding a product is written to the audit log (`PRODUCT_CREATED`).

**S2-3 Python version support**
- Type annotations are evaluated lazily in `repositories/base.py` and `services/prescriptions.py`, so the application starts on Python 3.11 as well as 3.14. Behaviour is unchanged.

## Definition of done (used at the review)

- Permissions are checked on the server, not only hidden in the screen.
- Input is validated and errors are shown to the user in plain language.
- Changes are written to the audit log and the audit chain still verifies.
- Existing Sprint 1 behaviour is unchanged, and an existing database still opens.
- The feature was exercised through the API and in a real browser before the review.
- The demo works on a clean database.

## Velocity

**Definition.** Velocity of a sprint = the sum of the story points of the
stories that are **completely done** at the sprint review. A story that is only
partly done counts as 0.

**Sprint 1** (from [SPRINT.md](SPRINT.md)). USER-13 (8 pts) is only the umbrella
of USER-20 and USER-21, so the sprint counts its two children, not the umbrella:

| Story size | Number of stories | Points |
|---|---|---|
| 5 points | 6 | 6 × 5 = 30 |
| 3 points | 7 | 7 × 3 = 21 |
| 2 points | 3 | 3 × 2 = 6 |
| 1 point | 4 | 4 × 1 = 4 |
| **Total** | **20** | **61** |

Sprint 1 velocity = **61**. This was the starting point for planning Sprint 2.

**Sprint 2**

| Story | Planned | Done |
|---|---|---|
| S2-1 Supplier directory | 5 | 5 |
| S2-2 Add a new medicine | 2 | 2 |
| S2-3 Python version support | 1 | 1 |
| **Total** | **8** | **8** |

Sprint 2 velocity = **8** (planned 8, done 8, nothing carried over).

**Average velocity** = (61 + 8) ÷ 2 sprints = **34.5 points per sprint**.

Sprint 1 built the whole backlog from scratch, while Sprint 2 was a small,
focused increment chosen from the gaps found in review, so the Sprint 2 figure
should not be read as the team's capacity.

## Sprint review

**Demo script** (start from a clean database with `python -m pharmacy --reset`):

1. Sign in as **owner** → **Suppliers**: contact details and price lists for the two demo suppliers; add a supplier and set a price.
2. **Recommended: Reorder** → choose a supplier → create the purchase order → **Purchase Inventory**: the lines show the supplier's price and where it came from.
3. **Products** → add a new medicine.
4. Sign out, sign in as **karim** (technician) → **Check by Bar Code** → scan the new medicine's barcode and receive stock.
5. **Inventory**: the new medicine is listed with its lot and quantity.
6. Sign in as **leila** (pharmacist): no Suppliers or Products tile — role permissions are unchanged.

Demo accounts are listed in [README.md](README.md).

## Retrospective

**What went well**
- Making every change additive kept the Sprint 1 demo working.
- Running the application and reviewing it as a user is what revealed both functional gaps: suppliers were only a typed name, and a new medicine could not be added from the application.

**What to improve**
- Run the application on both Python 3.11 and 3.14 before every review (a start-up error on 3.11 was found late).
- Agree the story estimates with the whole team at planning, before the sprint starts.

## Product backlog (not in Sprint 2)

Gaps found during the review. Estimates are proposals.

| Item | Proposed pts | Note |
|---|---|---|
| Change-password screen that asks for the current password | 3 | The server lets a signed-in user change their own password, but there is no screen and the current password is not asked for. |
| Screen to edit a product's reorder point, safety stock and lead time | 2 | The server operation already exists; only the screen is missing. |
| Refunds and voids for sales | 5 | Listed as out of scope in Sprint 1. |
