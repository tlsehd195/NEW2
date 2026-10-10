# ADR-0063: 확신도별 거래당 위험(1~5%)과 완화된 손실 한도

**Status:** Accepted
**Date:** 2026-10-10
**Deciders:** account owner (2026-10-10 대화, 선택지 A), Claude Code session

## Context

사용자는 레버리지를 20배 고정이 아니라 판단 확신도에 따라 달리하고, 안전 한도를 더 느슨하게 하라고 정했다.
레버리지는 `위험% / 손절%`이므로 확신도에 따라 바꾸려면 위험%를 바꾸는 것이 유일한 방법이다(손절 1%에서 20배 = 거래당 20% 손실이라 채택하지 않음).

## Decision

- `RiskConfig`에 `risk_per_trade_max`, `risk_conf_low`, `risk_conf_high` 추가. 진입 쪽 확신도 P(롱은 p_long, 숏은 1-p_long)가
  `risk_conf_low` 이하면 `risk_per_trade`, `risk_conf_high` 이상이면 `risk_per_trade_max`, 사이는 선형. 확신도가 없으면
  `risk_per_trade`. `EntryRequest.confidence`로 전달하고(`strategies.base.entry_confidence`), 백테스트·페이퍼가 같은 경로를 쓴다.
- `configs/risk.json`: 위험 1%(P 60%) → 5%(P 75% 이상), `max_leverage` 20 유지.
- 한도 완화: `max_daily_loss` 10→15%, `max_drawdown` 15→25%(킬스위치 기준도 동일), 낙폭 가드 6→10%/48시간.
- 변경 없음: 손절 1~2% 제한, 청산가 ≥ 손절 3배 검사, 거래소 레버리지 20배, 스프레드 한도.

## Consequences

- 손절 1%에서 레버리지는 1~5배, 손절이 좁으면 더 커진다. 상한 20배는 유지.
- 최악의 거래 손실은 잔고의 5%(+비용). 15% 일일 한도는 3번째 연속 최대 손실 근처에서 닿는다.
- 신호 보정 결과(ADR-0042)는 P≥60% 구간 적중률 49~51%라, 확신도가 높을수록 더 잘 맞는다는 근거가 아직 없다.
  크게 거는 만큼 손실도 커지므로 모의투자에서 확인하기 전에는 실거래에 쓰지 않는다(ADR-0050 C안의 Kelly 논리와 상충: 측정된 p로는 기대 크기가 0).
- 손절 1.5% 초과 진입은 20배 마진 가정 때문에 여전히 청산 검사에서 막힌다(ADR-0059). 별도 결정 사항.
- `RiskConfig.version()`이 바뀌어 모의투자를 새로 시작해야 하고 이전 성과와 비교할 수 없다. 사전등록·예산 미사용.
