

def test_param_search_ids_are_distinct_and_default_ids_unchanged():
    from cointrader.strategies.daytrade import DayTradeVote
    base = DayTradeVote(horizon=16)
    assert base.strategy_id == "daytrade_indicator_vote_h16_c0.6_v1"
    ids = {DayTradeVote(horizon=16, **kw).strategy_id for kw in
           ({"max_hold_bars": 24}, {"max_hold_bars": 96}, {"enter_confidence": 0.55}, {"stop_atr": 1.5}, {"stop_atr": 3.5})}
    assert len(ids) == 5 and base.strategy_id not in ids
