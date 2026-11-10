# Pharmacy Management Software

Sprint 1 implementation of the Jira backlog **USER** ("Pharmacy Management
Software"), following the Figma prototype *Prototype-1 → assignment 3 extended
prototype*. See [SPRINT.md](SPRINT.md) for the ranked backlog, the acceptance
criteria the code enforces, and deviations from the prototype.

- **Pure Python standard library** (developed and checked on Python 3.14; needs 3.10+). No packages to install: `sqlite3`
  for storage, `http.server` for the API, and a static HTML/JS front end.
- **Repository pattern.** Services never touch storage directly. The same
  repositories run over an in-memory store (tests) or SQLite (the app).
- **Server-side authorisation.** Every service method takes the acting user
  and checks permissions itself; the UI only hides what is not allowed.

## Run it

```bash
python3 -m pharmacy
```

Open http://127.0.0.1:8000. The first run creates `data/pharmacy.db` and
loads about 40 days of demo activity, so every queue has work in it. The
login screen has one-click buttons for each demo account:

| Username | Password | Role |
|---|---|---|
| owner | owner-demo-1 | Owner |
| manager | manager-demo-1 | Manager |
| leila | pharmacist-demo-1 | Pharmacist |
| karim | technician-demo-1 | Technician |
| cashier | cashier-demo-1 | Cashier |
| maya | patient-demo-1 | Patient (Maya Haddad) |

These are local demo credentials only. Other options:

```bash
python3 -m pharmacy --memory          # throwaway in-memory database
python3 -m pharmacy --reset           # delete data/pharmacy.db and reseed
python3 -m pharmacy --no-seed --db x.db --port 9000
```

## Project layout

```
pharmacy/
  domain/        enums.py, models.py      entities as dataclasses (no I/O)
  repositories/  stores.py                MemoryStore, SqliteStore, transactions
                 base.py                  Repository[T], AppendOnlyRepository[T]
                 domain.py                one repository per aggregate + its queries
                 codec.py                 dataclass <-> JSON via type hints
  services/      access.py                US1.1 roles/permissions, US1.2 dashboard
                 patients.py              US2.1 profiles, US2.2 clinical view
                 prescriptions.py         US3.1 workflow, US3.2 verification gate
                 clinical_alerts.py       US4.1 screening evidence, US4.2 resolution
                 barcode.py, inventory.py US5.1 GS1 lot scanner, US5.2 stock alerts
                 purchasing.py            US6.1 explained suggestions, US6.2 PO decisions
                 refills.py               US7.1 refill request + order tracking
                 notifications.py         US7.2 channel and consent
                 sales.py                 US8.1 payments and receipts
                 reports.py, audit.py     US8.2 reports, hash-chained audit log
                 __init__.py              PharmacyApp: wires everything together
  api/           routes.py                transport-independent JSON API (Api.handle)
                 server.py                http.server adapter + static files
  web/           index.html, app.js, styles.css
  seed.py        demo data, created through the real services
```

## Testing notes

There is no test suite in this sprint; it is being written separately. The
code is set up for it:

```python
from datetime import datetime, timezone
from pharmacy.clock import FixedClock
from pharmacy.repositories import Repositories
from pharmacy.services import PharmacyApp
from pharmacy.api import Api

repos = Repositories.in_memory()                    # or Repositories.sqlite(":memory:")
clock = FixedClock(datetime(2026, 11, 10, 12, tzinfo=timezone.utc))
app = PharmacyApp(repos, clock)                      # service layer
api = Api(app)                                       # HTTP-free API layer

status, body = api.handle("POST", "/api/login", body={"username": "...", "password": "..."})
```

- **Bootstrap without seed data.** `app.access.create_user(None, ...)` and
  `app.inventory.add_product(None, ...)` accept `actor=None` for set-up.
  Every other call needs a user.
- **Time.** `FixedClock.advance(days=..)` moves time. Expiry, validity,
  active-medication and usage-window rules all read the injected clock.
- **Seeded fixtures.** `pharmacy.seed.seed(repos, now)` builds the full demo
  state relative to `now`; `DEMO_ACCOUNTS` holds the credentials.
- **Errors.** Services raise `pharmacy.errors.*`. The API maps them to
  400 / 401 / 403 / 404 / 409.
- **Transactions.** `Api.handle` runs each request in
  `repos.transaction()`. A failing request leaves no partial writes or
  audit entries, on both backends.
- **Audit integrity.** `app.audit.verify_chain(owner)` recomputes the hash
  chain and reports the first altered or missing entry.

## API overview

Every endpoint except login, policy, branches and demo-accounts needs
`Authorization: Bearer <token>` from `POST /api/login`.

| Area | Endpoints |
|---|---|
| Session | `POST /api/login`, `POST /api/logout`, `GET /api/me` |
| Access | `GET/POST /api/users`, `POST /api/users/{id}/role\|permissions\|deactivate\|reactivate\|password`, `GET /api/permissions` |
| Patients | `GET/POST /api/patients`, `GET/PATCH /api/patients/{id}`, `POST /api/patients/{id}/allergies`, `DELETE /api/patients/{id}/allergies/{substance}`, `GET /api/patients/{id}/clinical`, `GET/POST /api/prescribers`, `PATCH /api/prescribers/{id}` |
| Prescriptions | `GET/POST /api/prescriptions`, `GET/PATCH /api/prescriptions/{id}`, `POST /api/prescriptions/{id}/transition`, `GET /api/verification-queue` |
| Alerts | `GET /api/alerts`, `POST /api/alerts/{id}/resolve`, `GET /api/alerts/guidance`, `GET /api/interaction-rules` |
| Inventory | `GET/POST /api/products`, `PATCH /api/products/{id}/reorder`, `GET /api/inventory`, `POST /api/inventory/scan/preview\|receive-scan\|receive`, `GET /api/inventory/alerts` |
| Purchasing | `GET /api/reorder-suggestions`, `GET/POST /api/purchase-orders`, `POST /api/purchase-orders/{id}/approve\|reject\|received` |
| Patient | `GET /api/me/refillable\|refills\|orders\|notifications\|notification-preferences`, `POST /api/me/refills`, `POST /api/me/refills/{id}/cancel`, `POST /api/me/notification-preferences/channel`, `POST /api/me/consent`, `POST /api/me/consent/withdraw` |
| Refill review | `GET /api/refills/queue`, `POST /api/refills/{id}/start-review\|approve\|reject` |
| Sales | `GET /api/sales/payable`, `POST /api/sales/prescription\|otc`, `GET /api/sales`, `GET /api/receipts/{number}` |
| Reports | `GET /api/reports/sales\|operations\|alert-overrides`, `GET /api/audit`, `GET /api/audit/verify` |

## Known limits

- Queries scan whole tables. That is fine at pharmacy scale; indexed queries
  would be the next step for SQLite.
- Sessions live in server memory, so a restart signs everyone out.
- SMS and email delivery are simulated: messages are stored as sent or
  suppressed.
- Seeded interaction rules are demo data, not a clinical reference.
