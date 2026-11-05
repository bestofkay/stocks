"""Helpers for statement-import connectors (CSV/PDF downloadable statements)."""

from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation
from typing import Iterable

from app.connectors.models import NormalizedHolding


def parse_decimal_safe(value: str | None) -> Decimal | None:
    if value is None:
        return None
    text = value.strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def holdings_from_csv(
    content: str | bytes,
    *,
    symbol_col: str = "symbol",
    name_col: str = "name",
    quantity_col: str = "quantity",
    value_col: str = "market_value",
    currency_col: str = "currency",
    default_currency: str | None = None,
) -> list[NormalizedHolding]:
    """Parse a simple holdings CSV into normalized holdings.

    Unknown / missing prices remain None (never coerced to zero).
    """
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    holdings: list[NormalizedHolding] = []
    for row in reader:
        name = (row.get(name_col) or row.get(symbol_col) or "").strip()
        if not name:
            continue
        symbol = (row.get(symbol_col) or "").strip() or None
        holdings.append(
            NormalizedHolding(
                symbol=symbol,
                security_name=name,
                quantity=parse_decimal_safe(row.get(quantity_col)),
                market_value=parse_decimal_safe(row.get(value_col)),
                currency=(row.get(currency_col) or default_currency or None),
            )
        )
    return holdings


def iter_nonempty_rows(rows: Iterable[dict[str, str]]) -> Iterable[dict[str, str]]:
    for row in rows:
        if any((v or "").strip() for v in row.values()):
            yield row
