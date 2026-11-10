"""Generic repository over a ``Store``.

Repositories hand out decoded copies, so mutating an entity has no effect
until it is passed back through ``update``/``save``. This keeps the
in-memory backend behaving like a real database in tests.
"""

from typing import Callable, Generic, Iterable, Optional, TypeVar

from ..errors import ConflictError, NotFoundError
from . import codec
from .stores import Store

T = TypeVar("T")


class Repository(Generic[T]):
    entity_name: str = "Entity"

    def __init__(self, store: Store, entity_cls: type[T]):
        self._store = store
        self._cls = entity_cls
        self.entity_name = entity_cls.__name__

    # ---- reads -----------------------------------------------------------
    def find(self, entity_id: str) -> Optional[T]:
        payload = self._store.get(entity_id)
        return codec.loads(self._cls, payload) if payload is not None else None

    def get(self, entity_id: str) -> T:
        entity = self.find(entity_id)
        if entity is None:
            raise NotFoundError(f"{self.entity_name} {entity_id} not found")
        return entity

    def exists(self, entity_id: str) -> bool:
        return self._store.get(entity_id) is not None

    def list(self) -> list[T]:
        return [codec.loads(self._cls, p) for p in self._store.values()]

    def filter(self, predicate: Callable[[T], bool]) -> list[T]:
        return [e for e in self.list() if predicate(e)]

    def first(self, predicate: Callable[[T], bool]) -> Optional[T]:
        for entity in self.list():
            if predicate(entity):
                return entity
        return None

    def count(self, predicate: Optional[Callable[[T], bool]] = None) -> int:
        if predicate is None:
            return self._store.count()
        return len(self.filter(predicate))

    def last(self) -> Optional[T]:
        payload = self._store.last()
        return codec.loads(self._cls, payload) if payload is not None else None

    # ---- writes ----------------------------------------------------------
    def add(self, entity: T) -> T:
        entity_id = getattr(entity, "id")
        if self.exists(entity_id):
            raise ConflictError(f"{self.entity_name} {entity_id} already exists")
        self._store.put(entity_id, codec.dumps(entity))
        return entity

    def add_all(self, entities: Iterable[T]) -> None:
        for entity in entities:
            self.add(entity)

    def update(self, entity: T) -> T:
        entity_id = getattr(entity, "id")
        if not self.exists(entity_id):
            raise NotFoundError(f"{self.entity_name} {entity_id} not found")
        self._store.put(entity_id, codec.dumps(entity))
        return entity

    def save(self, entity: T) -> T:
        self._store.put(getattr(entity, "id"), codec.dumps(entity))
        return entity

    def delete(self, entity_id: str) -> None:
        if not self._store.delete(entity_id):
            raise NotFoundError(f"{self.entity_name} {entity_id} not found")


class AppendOnlyRepository(Repository[T]):
    """Repository whose records can be added but never changed or removed."""

    def update(self, entity: T) -> T:
        raise ConflictError(f"{self.entity_name} records are append-only")

    def save(self, entity: T) -> T:
        if self.exists(getattr(entity, "id")):
            raise ConflictError(f"{self.entity_name} records are append-only")
        return self.add(entity)

    def delete(self, entity_id: str) -> None:
        raise ConflictError(f"{self.entity_name} records are append-only")
