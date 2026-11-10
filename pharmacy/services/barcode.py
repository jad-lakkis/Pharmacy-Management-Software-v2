"""Lot scanner (USER-22): parse GS1 barcodes used on medicine packs.

Supported input forms:

* Human-readable: ``(01)05012345678900(17)271231(10)LOT42``
* Raw scanner output with FNC1/GS (``\\x1d``) separators, optionally with
  a ``]d2``/``]C1`` symbology prefix: ``010501234567890017271231\x1d10LOT42``
* ``|`` as a typed stand-in for the GS separator.
* A bare 8/12/13/14-digit GTIN/EAN/UPC (lot and expiry entered by hand).

Application identifiers handled: 01 (GTIN), 17 (expiry YYMMDD), 10 (lot),
21 (serial). Unknown identifiers are rejected rather than guessed.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from ..errors import ValidationError

GS = "\x1d"
_FIXED_LENGTH = {"01": 14, "17": 6}
_VARIABLE = {"10": 20, "21": 20}
_HUMAN_READABLE = re.compile(r"\((\d{2})\)([^()]*)")


@dataclass(frozen=True)
class ScanResult:
    gtin: str
    expiry_date: Optional[date] = None
    lot_number: Optional[str] = None
    serial: Optional[str] = None


def gtin_check_digit(body: str) -> int:
    """GS1 mod-10 check digit for the digits *before* the check digit."""
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10


def normalize_gtin(code: str) -> str:
    """Validate a GTIN-8/12/13/14 and return it zero-padded to 14 digits."""
    code = (code or "").strip()
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        raise ValidationError("barcode must be an 8, 12, 13 or 14 digit GTIN")
    if gtin_check_digit(code[:-1]) != int(code[-1]):
        raise ValidationError("barcode check digit is invalid (mis-scan?)")
    return code.zfill(14)


def parse_gs1_date(value: str) -> date:
    """YYMMDD; day ``00`` means the last day of the month (GS1 rule).

    Years use the GS1 sliding window; for pharmacy stock we simply map
    YY to 2000+YY, which is valid until 2099.
    """
    if len(value) != 6 or not value.isdigit():
        raise ValidationError("expiry must be 6 digits (YYMMDD)")
    year, month, day = 2000 + int(value[:2]), int(value[2:4]), int(value[4:])
    if not 1 <= month <= 12:
        raise ValidationError("expiry month is invalid")
    if day == 0:
        day = calendar.monthrange(year, month)[1]
    try:
        return date(year, month, day)
    except ValueError:
        raise ValidationError("expiry date is invalid") from None


def validate_lot(lot: str) -> str:
    lot = lot.strip().upper()
    if not lot or len(lot) > 20 or not re.fullmatch(r"[A-Z0-9\-./]+", lot):
        raise ValidationError("lot number must be 1-20 characters (letters, digits, - . /)")
    return lot


def _parse_elements(pairs: list[tuple[str, str]]) -> ScanResult:
    found: dict[str, str] = {}
    for ai, value in pairs:
        if ai not in _FIXED_LENGTH and ai not in _VARIABLE:
            raise ValidationError(f"unsupported GS1 application identifier ({ai})")
        if ai in found:
            raise ValidationError(f"application identifier ({ai}) appears twice")
        found[ai] = value
    if "01" not in found:
        raise ValidationError("scan does not contain a GTIN (01)")
    return ScanResult(
        gtin=normalize_gtin(found["01"]),
        expiry_date=parse_gs1_date(found["17"]) if "17" in found else None,
        lot_number=validate_lot(found["10"]) if "10" in found else None,
        serial=found.get("21"),
    )


def _parse_raw(data: str) -> ScanResult:
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(data):
        if data[i] == GS:
            i += 1
            continue
        ai = data[i:i + 2]
        i += 2
        if ai in _FIXED_LENGTH:
            length = _FIXED_LENGTH[ai]
            value = data[i:i + length]
            if len(value) != length:
                raise ValidationError(f"GS1 field ({ai}) is truncated")
            i += length
        elif ai in _VARIABLE:
            end = data.find(GS, i)
            end = len(data) if end == -1 else end
            value = data[i:end]
            if len(value) > _VARIABLE[ai]:
                raise ValidationError(f"GS1 field ({ai}) is too long; missing separator?")
            i = end
        else:
            raise ValidationError(f"unsupported GS1 application identifier ({ai})")
        pairs.append((ai, value))
    return _parse_elements(pairs)


def parse_scan(raw: str) -> ScanResult:
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationError("scan is empty")
    data = raw.strip()
    for prefix in ("]d2", "]C1", "]Q3", "]e0"):
        if data.startswith(prefix):
            data = data[len(prefix):]
            break
    if data.startswith("("):
        pairs = _HUMAN_READABLE.findall(data)
        if not pairs or "".join(f"({a}){v}" for a, v in pairs) != data:
            raise ValidationError("could not read the human-readable GS1 code")
        return _parse_elements(pairs)
    data = data.replace("|", GS)
    if data.isdigit() and len(data) in (8, 12, 13, 14):
        # A bare GTIN. (An AI-01 element is 16 digits, so this is unambiguous.)
        return ScanResult(gtin=normalize_gtin(data))
    return _parse_raw(data)
