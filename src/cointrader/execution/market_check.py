"""Compare configs/markets.json with Binance's exchangeInfo filters (T7).

Pure. The network call lives in `scripts/verify_markets.py`, which has to run
on a machine that can reach fapi.binance.com (the cloud sandbox and GitHub
Actions cannot, ADR-0012).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from cointrader.risk.engine import SymbolFilters

FIELDS = ("tick_size", "step_size", "min_quantity", "min_notional")  # same names in the config and SymbolFilters


@dataclass(frozen=True)
class Difference:
    symbol: str
    field: str
    configured: float
    exchange: float


def compare_markets(config_markets: Mapping[str, Mapping], filters: Mapping[str, SymbolFilters]
                    ) -> tuple[list[Difference], list[str]]:
    """-> (differences, symbols that are in the config but not TRADING on the exchange)."""
    diffs, missing = [], []
    for sym, rules in config_markets.items():
        f = filters.get(sym)
        if f is None:
            missing.append(sym)
            continue
        for key in FIELDS:
            ex = float(getattr(f, key))
            if not math.isclose(float(rules[key]), ex, rel_tol=1e-9, abs_tol=1e-12):
                diffs.append(Difference(sym, key, float(rules[key]), ex))
    return diffs, missing
