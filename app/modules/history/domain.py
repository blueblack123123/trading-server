import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True, slots=True)
class SaleRecord:
    item_id: str
    sold_at: datetime
    amount: int
    price: Decimal
    quality: int | None
    additional: dict[str, Any]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class LotRecord:
    item_id: str
    amount: int
    start_price: Decimal
    buyout_price: Decimal
    start_time: datetime
    end_time: datetime
    quality: int | None
    additional: dict[str, Any]
    fingerprint: str


def parse_sale(item_id: str, payload: dict[str, Any]) -> SaleRecord:
    sold_at = _parse_datetime(payload.get("time"))
    amount = _parse_positive_int(payload.get("amount"), "amount")
    price = _parse_decimal(payload.get("price"))

    raw_additional = payload.get("additional")
    additional = raw_additional if isinstance(raw_additional, dict) else {}
    quality = _parse_quality(additional.get("qlt"))

    canonical = json.dumps(additional, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint_source = "|".join(
        (
            item_id,
            sold_at.isoformat(),
            str(amount),
            format(price, "f"),
            "none" if quality is None else str(quality),
            canonical,
        )
    )
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()

    return SaleRecord(
        item_id=item_id,
        sold_at=sold_at,
        amount=amount,
        price=price,
        quality=quality,
        additional=additional,
        fingerprint=fingerprint,
    )


def parse_lot(item_id: str, payload: dict[str, Any]) -> LotRecord:
    amount = _parse_positive_int(payload.get("amount"), "amount")
    start_price = _parse_decimal(payload.get("startPrice", 0))
    buyout_price = _parse_decimal(payload.get("buyoutPrice"))
    start_time = _parse_datetime(payload.get("startTime"))
    end_time = _parse_datetime(payload.get("endTime"))

    raw_additional = payload.get("additional")
    additional = raw_additional if isinstance(raw_additional, dict) else {}
    quality = _parse_quality(additional.get("qlt"))
    canonical = json.dumps(additional, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint_source = "|".join(
        (
            item_id,
            str(amount),
            format(start_price, "f"),
            format(buyout_price, "f"),
            start_time.isoformat(),
            end_time.isoformat(),
            "none" if quality is None else str(quality),
            canonical,
        )
    )
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()

    return LotRecord(
        item_id=item_id,
        amount=amount,
        start_price=start_price,
        buyout_price=buyout_price,
        start_time=start_time,
        end_time=end_time,
        quality=quality,
        additional=additional,
        fingerprint=fingerprint,
    )


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("history entry has no valid time")

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"history entry has no valid {field}")
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"history entry has no valid {field}") from exc
    if parsed <= 0:
        raise ValueError(f"history entry has no valid {field}")
    return parsed


def _parse_decimal(value: object) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("history entry has no valid price") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("history entry has no valid price")
    return parsed


def _parse_quality(value: object) -> int | None:
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (str, int, float, Decimal))
    ):
        return None
    try:
        quality = int(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return quality if 0 <= quality <= 255 else None
