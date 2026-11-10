"""Append-only, hash-chained audit log (US8.2)."""

import hashlib
import json
from datetime import datetime
from typing import Any, Optional

from ..domain.enums import Permission
from ..domain.models import AuditEntry, User
from ..repositories import codec
from .context import Service

GENESIS_HASH = "0" * 64


def _entry_hash(entry: AuditEntry) -> str:
    payload = codec.to_primitive(entry)
    payload.pop("entry_hash", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditService(Service):
    def record(
        self,
        actor: Optional[User],
        action: str,
        entity_type: str,
        entity_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> AuditEntry:
        """Append one entry. Called by services inside their transaction, so
        a failed operation leaves no audit entry behind."""
        previous = self.repos.audit.last()
        sequence = previous.sequence + 1 if previous else 1
        entry = AuditEntry(
            id=f"AUD-{sequence:07d}",
            sequence=sequence,
            at=self.clock.now(),
            actor_id=actor.id if actor else None,
            actor_name=actor.display_name if actor else "system",
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=codec.to_primitive(details or {}),
            prev_hash=previous.entry_hash if previous else GENESIS_HASH,
        )
        entry.entry_hash = _entry_hash(entry)
        self.repos.audit.add(entry)
        return entry

    def query(
        self,
        actor: User,
        *,
        actor_id: Optional[str] = None,
        action: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 200,
    ) -> list[AuditEntry]:
        """Newest first."""
        self.access.require(actor, Permission.AUDIT_VIEW)

        def matches(e: AuditEntry) -> bool:
            return (
                (actor_id is None or e.actor_id == actor_id)
                and (action is None or e.action == action)
                and (entity_type is None or e.entity_type == entity_type)
                and (entity_id is None or e.entity_id == entity_id)
                and (start is None or e.at >= start)
                and (end is None or e.at <= end)
            )

        entries = [e for e in self.repos.audit.list() if matches(e)]
        entries.sort(key=lambda e: e.sequence, reverse=True)
        return entries[: max(1, min(limit, 1000))]

    def verify_chain(self, actor: User) -> dict[str, Any]:
        """Recompute every hash; report the first broken link, if any."""
        self.access.require(actor, Permission.AUDIT_VIEW)
        expected_prev = GENESIS_HASH
        entries = sorted(self.repos.audit.list(), key=lambda e: e.sequence)
        for index, entry in enumerate(entries, start=1):
            if entry.sequence != index:
                return {"valid": False, "checked": index - 1, "broken_at": entry.id,
                        "reason": "sequence gap (entry missing)"}
            if entry.prev_hash != expected_prev:
                return {"valid": False, "checked": index - 1, "broken_at": entry.id,
                        "reason": "previous-hash mismatch"}
            if _entry_hash(entry) != entry.entry_hash:
                return {"valid": False, "checked": index - 1, "broken_at": entry.id,
                        "reason": "content hash mismatch (entry altered)"}
            expected_prev = entry.entry_hash
        return {"valid": True, "checked": len(entries), "broken_at": None, "reason": None}
