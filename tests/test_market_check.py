from cointrader.execution.binance_client import parse_exchange_info
from cointrader.execution.market_check import compare_markets


def _info(tick, step, qty, notional, status="TRADING"):
    return {"symbols": [{"symbol": "BTCUSDT", "status": status, "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": tick}, {"filterType": "LOT_SIZE", "stepSize": step, "minQty": qty},
        {"filterType": "MIN_NOTIONAL", "notional": notional}]}]}


CFG = {"BTCUSDT": {"tick_size": 0.1, "step_size": 0.001, "min_quantity": 0.001, "min_notional": 100.0,
                   "large_trade_quantity": 2.0}}


def test_matching_rules():
    assert compare_markets(CFG, parse_exchange_info(_info("0.10", "0.001", "0.001", "100"))) == ([], [])


def test_differences_and_missing_symbols():
    diffs, missing = compare_markets({**CFG, "ETHUSDT": CFG["BTCUSDT"]},
                                     parse_exchange_info(_info("0.10", "0.001", "0.002", "5")))
    assert [(d.field, d.exchange) for d in diffs] == [("min_quantity", 0.002), ("min_notional", 5.0)]
    assert missing == ["ETHUSDT"]


def test_not_trading_counts_as_missing():
    assert compare_markets(CFG, parse_exchange_info(_info("0.1", "0.001", "0.001", "100", "SETTLING")))[1] == ["BTCUSDT"]
