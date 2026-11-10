"""Small input-validation helpers that raise ``ValidationError``."""

import re
from datetime import date
from enum import Enum
from typing import Any, Optional, TypeVar

from ..errors import ValidationError

E = TypeVar("E", bound=Enum)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^\+?[0-9][0-9 \-()]{5,19}$")


def require_text(value: Any, field: str, max_length: int = 200, min_length: int = 1) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} is required")
    text = value.strip()
    if len(text) < min_length:
        if min_length <= 1:
            raise ValidationError(f"{field} is required")
        raise ValidationError(f"{field} must be at least {min_length} characters")
    if len(text) > max_length:
        raise ValidationError(f"{field} must be at most {max_length} characters")
    return text


def optional_text(value: Any, field: str, max_length: int = 200) -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return require_text(value, field, max_length)


def require_int(value: Any, field: str, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValidationError(f"{field} must be a whole number")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a whole number") from None
    if minimum is not None and number < minimum:
        raise ValidationError(f"{field} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise ValidationError(f"{field} must be at most {maximum}")
    return number


def require_date(value: Any, field: str) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise ValidationError(f"{field} must be a date (YYYY-MM-DD)")


def require_enum(enum_cls: type[E], value: Any, field: str) -> E:
    if isinstance(value, enum_cls):
        return value
    for candidate in (value, str(value).strip(), str(value).strip().upper()):
        try:
            return enum_cls(candidate)
        except ValueError:
            continue
    allowed = ", ".join(m.value for m in enum_cls)
    raise ValidationError(f"{field} must be one of: {allowed}")


def optional_email(value: Any, field: str = "email") -> Optional[str]:
    text = optional_text(value, field, 254)
    if text is not None and not _EMAIL.match(text):
        raise ValidationError(f"{field} is not a valid email address")
    return text.lower() if text else None


def optional_phone(value: Any, field: str = "phone") -> Optional[str]:
    text = optional_text(value, field, 20)
    if text is not None and not _PHONE.match(text):
        raise ValidationError(f"{field} is not a valid phone number")
    return text
