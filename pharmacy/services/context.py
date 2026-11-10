"""Shared dependencies handed to every service."""

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from ..clock import Clock
from ..repositories import Repositories

if TYPE_CHECKING:  # pragma: no cover
    from .access import AccessService
    from .audit import AuditService


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def newest_first(items, key):
    """Sort newest first; records with equal timestamps keep reverse
    insertion order, so the most recently stored one comes first."""
    return sorted(reversed(list(items)), key=key, reverse=True)


@dataclass
class ServiceContext:
    repos: Repositories
    clock: Clock
    audit: Optional["AuditService"] = field(default=None)
    access: Optional["AccessService"] = field(default=None)


class Service:
    def __init__(self, ctx: ServiceContext):
        self.ctx = ctx

    @property
    def repos(self) -> Repositories:
        return self.ctx.repos

    @property
    def clock(self) -> Clock:
        return self.ctx.clock

    @property
    def audit(self) -> "AuditService":
        assert self.ctx.audit is not None
        return self.ctx.audit

    @property
    def access(self) -> "AccessService":
        assert self.ctx.access is not None
        return self.ctx.access
