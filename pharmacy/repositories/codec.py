"""Convert dataclass entities to JSON-safe primitives and back.

Decoding is driven by type hints, so entities need no hand-written
serialisers. Supported: dataclasses, Enum, datetime, date, list[X],
dict[str, X], Optional[X], Any and JSON scalars.
"""

import dataclasses
import enum
import json
import types
import typing
from datetime import date, datetime
from functools import lru_cache
from typing import Any


def to_primitive(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_primitive(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_primitive(v) for v in value]
    if isinstance(value, dict):
        return {str(to_primitive(k)): to_primitive(v) for k, v in value.items()}
    return value


@lru_cache(maxsize=None)
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def from_primitive(tp: Any, data: Any) -> Any:
    if data is None:
        return None
    if tp is Any:
        return data
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        options = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(options) != 1:
            raise TypeError(f"Only Optional[X] unions are supported, got {tp!r}")
        return from_primitive(options[0], data)
    if origin is list:
        (item_type,) = typing.get_args(tp) or (Any,)
        return [from_primitive(item_type, v) for v in data]
    if origin is dict:
        _, value_type = typing.get_args(tp) or (str, Any)
        return {k: from_primitive(value_type, v) for k, v in data.items()}
    if isinstance(tp, type):
        if dataclasses.is_dataclass(tp):
            hints = _hints(tp)
            kwargs = {
                f.name: from_primitive(hints[f.name], data[f.name])
                for f in dataclasses.fields(tp)
                if f.name in data
            }
            return tp(**kwargs)
        if issubclass(tp, enum.Enum):
            return tp(data)
        if tp is datetime:
            return datetime.fromisoformat(data)
        if tp is date:
            return date.fromisoformat(data)
        if tp in (int, float, str, bool):
            return tp(data)
    return data


def dumps(entity: Any) -> str:
    return json.dumps(to_primitive(entity), sort_keys=True, separators=(",", ":"))


def loads(cls: type, payload: str) -> Any:
    return from_primitive(cls, json.loads(payload))
