# ADR-0062: 레버리지 상한·거래소 설정 20배 → 10배

**Status:** Accepted
**Date:** 2026-10-10
**Deciders:** account owner (2026-10-10 대화, 선택지 A), Claude Code session

## Context

ADR-0059가 상한과 거래소 레버리지를 20배로 정했다. 그러나 포지션 레버리지는 `risk_per_trade / 손절%`
(= 2% / 1~2% = 약 1~2배)로 정해지므로 20배 천장은 한 번도 닿지 않는다. 20배가 실제로 영향을 주는 곳은
청산가 확인뿐이다: 마진을 명목금액/20으로 계산해 청산가가 약 4.5% 거리에 놓이고, "청산가 >= 손절거리 3배"
규칙 때문에 손절이 약 1.5%를 넘는 진입이 막혔다(ADR-0060이 허용한 1~2% 범위의 윗부분이 사실상 쓰이지 않음).

## Decision

- `configs/risk.json`: `max_leverage` 20 → 10.
- `configs/margin_policy.json`: `exchange_leverage` 20 → 10 (상한과 같은 값 유지, 테스트로 고정되어 있음).
- 청산가 3배 규칙은 그대로. 포지션 크기 공식, 위험 2%, 손절 1~2%는 변경 없음.

## Consequences

- 포지션 크기는 변하지 않는다. 손절 1~2% 전 구간이 청산가 확인을 통과한다(가정 등급표 기준 BTC/ETH 롱·숏 직접 계산).
  등급표는 verified=false이며 라이브 전 실제 leverageBracket으로 다시 확인해야 한다.
- `RiskConfig.version()`이 바뀌므로 모의투자를 새 설정으로 다시 시작해야 한다(이전 성과와 비교 불가).
- 라이브에서는 Binance 심볼별 레버리지를 10으로 맞춰야 한다(`margin_settings_mismatches`).
- 손절이 약 0.1% 미만으로 좁아지는 경우에만 상한 10배가 걸린다(현재 하한 1%라 해당 없음).
