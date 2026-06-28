from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.modules.history.domain import parse_lot, parse_sale


def test_parse_sale_extracts_quality_and_normalizes_time() -> None:
    sale = parse_sale(
        "item-1",
        {
            "amount": 2,
            "price": 12345.0,
            "time": "2026-06-28T10:11:12Z",
            "additional": {"qlt": 4, "buyer": "test"},
        },
    )

    assert sale.sold_at == datetime(2026, 6, 28, 10, 11, 12, tzinfo=UTC)
    assert sale.amount == 2
    assert sale.price == Decimal("12345.0")
    assert sale.quality == 4


def test_fingerprint_is_stable_for_reordered_additional_fields() -> None:
    base = {
        "amount": 1,
        "price": 100,
        "time": "2026-06-28T10:11:12Z",
    }
    first = parse_sale("item-1", {**base, "additional": {"qlt": 2, "buyer": "a"}})
    second = parse_sale("item-1", {**base, "additional": {"buyer": "a", "qlt": 2}})

    assert first.fingerprint == second.fingerprint


def test_quality_changes_fingerprint() -> None:
    payload = {
        "amount": 1,
        "price": 100,
        "time": "2026-06-28T10:11:12Z",
        "additional": {"qlt": 1},
    }
    first = parse_sale("item-1", payload)
    payload["additional"] = {"qlt": 2}
    second = parse_sale("item-1", payload)

    assert first.fingerprint != second.fingerprint


@pytest.mark.parametrize("field,value", [("amount", 0), ("amount", -1), ("price", -1)])
def test_invalid_sale_is_rejected(field: str, value: int) -> None:
    payload = {
        "amount": 1,
        "price": 100,
        "time": "2026-06-28T10:11:12Z",
        "additional": {},
    }
    payload[field] = value

    with pytest.raises(ValueError):
        parse_sale("item-1", payload)


def test_parse_lot_extracts_quality_and_has_stable_fingerprint() -> None:
    payload = {
        "amount": 2,
        "startPrice": 10,
        "buyoutPrice": 500,
        "startTime": "2026-06-28T10:00:00Z",
        "endTime": "2026-06-30T10:00:00Z",
        "additional": {"qlt": 5, "buyer": "test"},
    }
    first = parse_lot("item-1", payload)
    payload["additional"] = {"buyer": "test", "qlt": 5}
    second = parse_lot("item-1", payload)

    assert first.quality == 5
    assert first.buyout_price == Decimal(500)
    assert first.fingerprint == second.fingerprint
