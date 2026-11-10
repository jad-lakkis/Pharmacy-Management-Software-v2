"""Pharmacy Management Software (Sprint 1).

Layers, from the inside out:

* ``pharmacy.domain``       – dataclass entities and enums (no I/O).
* ``pharmacy.repositories`` – repository pattern over pluggable stores
                              (in-memory for tests, SQLite for running).
* ``pharmacy.services``     – business rules for each user story; every
                              public method takes the acting ``User`` and
                              enforces permissions itself.
* ``pharmacy.api``          – stdlib HTTP/JSON adapter and static web UI.
"""

__version__ = "0.1.0"
