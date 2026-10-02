"""Central strategy registry (`configs/strategies.json`).

Nothing runs -- not in paper, never in live -- unless it is registered
here with a spec whose `strategy_id` matches what the code actually
builds. That catches silent drift: if someone changes a strategy's code
or defaults, its id (which encodes name, parameters and version) no
longer matches the registered one and the registry refuses it until a
new spec (and, for research, a new hypothesis id) is written.

The registry is NOT the source of a candidate's lifecycle status. Status
comes from the append-only candidate ledger (`research.lifecycle`), where
APPROVED/DEPLOYED can only be written by the human path
(`evolution.status.approve_for_live`). The registry only answers "is
this a known, unmodified strategy, and which family/timeframe/markets
is it for?".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from cointrader.strategies.indicator_vote import IndicatorVote
from cointrader.strategies.scalp import MicrostructureMomentum, RangeBreakoutVolume, ShortTermMeanReversion, VwapReversion
from cointrader.strategies.swing import (
    BollingerReversion, BreakoutVolume, DonchianTrend, RegimeHybrid, SmaTrendFilter, TrendEmaAtr, TrendPullback,
)

DEFAULT_REGISTRY = Path(__file__).resolve().parents[3] / "configs" / "strategies.json"

FACTORIES = {
    "TrendEmaAtr": TrendEmaAtr,
    "BreakoutVolume": BreakoutVolume,
    "BollingerReversion": BollingerReversion,
    "RegimeHybrid": RegimeHybrid,
    "SmaTrendFilter": SmaTrendFilter,
    "DonchianTrend": DonchianTrend,
    "TrendPullback": TrendPullback,
    "VwapReversion": VwapReversion,
    "RangeBreakoutVolume": RangeBreakoutVolume,
    "ShortTermMeanReversion": ShortTermMeanReversion,
    "MicrostructureMomentum": MicrostructureMomentum,
    "IndicatorVote": IndicatorVote,
}

FAMILIES = ("swing", "scalp", "daytrade")


class UnregisteredStrategy(LookupError):
    pass


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    name: str  # key into FACTORIES
    family: str
    timeframes: tuple[str, ...]
    markets: tuple[str, ...]
    version: str
    warmup: int
    parameters: dict = field(default_factory=dict)
    risk_profile: str = "default"
    hypothesis_id: Optional[str] = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.family not in FAMILIES:
            raise ValueError(f"unknown family {self.family!r}")
        if self.name not in FACTORIES:
            raise ValueError(f"unknown strategy class {self.name!r}")


def build(spec: StrategySpec):
    """Instantiate and verify the built strategy matches the spec."""
    params = dict(spec.parameters)
    params.setdefault("timeframe", spec.timeframes[0])
    strategy = FACTORIES[spec.name](**params)
    if strategy.strategy_id != spec.strategy_id:
        raise UnregisteredStrategy(
            f"code builds {strategy.strategy_id!r} but the registry says {spec.strategy_id!r}; "
            "register the new version explicitly"
        )
    if strategy.family != spec.family:
        raise UnregisteredStrategy(f"{spec.strategy_id}: family {strategy.family} != registered {spec.family}")
    if strategy.warmup != spec.warmup:
        raise UnregisteredStrategy(f"{spec.strategy_id}: warmup {strategy.warmup} != registered {spec.warmup}")
    return strategy


def spec_for(strategy, *, markets: tuple[str, ...], hypothesis_id: Optional[str] = None, notes: str = "") -> StrategySpec:
    """Spec of an existing instance (used to write the registry file)."""
    params = {k: v for k, v in strategy.parameters.items() if not isinstance(v, dict)}
    if any(isinstance(v, dict) for v in strategy.parameters.values()):
        params = {}  # composite strategies are registered with their fixed defaults
    return StrategySpec(strategy.strategy_id, type(strategy).__name__, strategy.family, (strategy.timeframe,), markets,
                        strategy.version, strategy.warmup, params, hypothesis_id=hypothesis_id, notes=notes)


class StrategyRegistry:
    def __init__(self, specs: list[StrategySpec]) -> None:
        ids = [s.strategy_id for s in specs]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate strategy_id in registry")
        self._specs = {s.strategy_id: s for s in specs}

    @classmethod
    def load(cls, path: Path = DEFAULT_REGISTRY) -> "StrategyRegistry":
        rows = json.loads(path.read_text(encoding="utf-8"))
        return cls([StrategySpec(
            strategy_id=r["strategy_id"], name=r["name"], family=r["family"], timeframes=tuple(r["timeframes"]),
            markets=tuple(r["markets"]), version=r["version"], warmup=r["warmup"], parameters=r.get("parameters", {}),
            risk_profile=r.get("risk_profile", "default"), hypothesis_id=r.get("hypothesis_id"), notes=r.get("notes", ""),
        ) for r in rows])

    def ids(self) -> list[str]:
        return sorted(self._specs)

    def get(self, strategy_id: str) -> StrategySpec:
        spec = self._specs.get(strategy_id)
        if spec is None:
            raise UnregisteredStrategy(f"{strategy_id!r} is not in the strategy registry")
        return spec

    def build(self, strategy_id: str, *, market: Optional[str] = None, family: Optional[str] = None):
        spec = self.get(strategy_id)
        if market is not None and market not in spec.markets:
            raise UnregisteredStrategy(f"{strategy_id} is not registered for {market}")
        if family is not None and spec.family != family:
            raise UnregisteredStrategy(f"{strategy_id} is a {spec.family} strategy, not {family}")
        return build(spec)

    def by_family(self, family: str) -> list[StrategySpec]:
        return [s for s in self._specs.values() if s.family == family]


def to_json(specs: list[StrategySpec]) -> str:
    return json.dumps([{
        "strategy_id": s.strategy_id, "name": s.name, "family": s.family, "timeframes": list(s.timeframes),
        "markets": list(s.markets), "version": s.version, "warmup": s.warmup, "parameters": s.parameters,
        "risk_profile": s.risk_profile, "hypothesis_id": s.hypothesis_id, "notes": s.notes,
    } for s in specs], ensure_ascii=False, indent=2) + "\n"
