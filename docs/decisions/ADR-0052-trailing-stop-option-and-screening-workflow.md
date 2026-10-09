# ADR-0052: 15분봉 투표에 추적 손절 옵션, 선별 워크플로

**Status:** Accepted (2026-10-09 동동님 "1": 켬/끔을 선별로 먼저 비교)
**Date:** 2026-10-09
**Deciders:** account owner (동동), Claude Code session

## Context

- 동동님 제안(2026-10-09): 방향이 맞아 계속 가는 거래는 오래 들고 가고 손절선을 따라 올린다(숏은 내린다).
- 엔진에는 이미 추적 손절이 있다: 백테스트 `event_engine.py`(최고가/최저가 − 거리로 손절선을 한쪽으로만 옮김),
  모의투자 `paper/engine.py`(최고가/최저가에서 거리만큼 되돌리면 `trailing_stop` 청산). 그러나 15분봉 투표
  (`DayTradeVote`)는 `trailing_distance`를 넘기지 않아 진입 때 정한 2.5 ATR 손절이 고정이다.
- 기본으로 켜면 모든 거래의 청산이 바뀌므로 새 후보다. 결선 예산은 2026-10-29까지 차 있어 선별(ADR-0051)로 먼저 본다.
- 이 클라우드 환경에서는 data.binance.vision 접속이 막혀(프록시 403) 선별을 GitHub Actions에서 돌려야 하는데,
  선별용 워크플로가 없었다.

## Decision

1. `IndicatorVote`에 `trail_atr`(기본 None = 지금과 같음)를 둔다. 값이 있으면 `trailing_distance = trail_atr × ATR`,
   롱·숏 둘 다. 설정됐을 때만 id(`_t2.5`)와 parameters에 나타나므로 기존 후보 id·기록은 그대로다.
2. 비교 후보 2개 등록(선별 전용, 사전등록 아님): `daytrade_indicator_vote_h16_c0.6_t2.5_v1`,
   `daytrade_indicator_vote_h48_c0.6_t2.5_v1`. 거리는 손절폭과 같은 2.5 ATR(진입 직후부터 손절선이 따라감).
   결과를 보고 고른 값이 아니다.
3. `.github/workflows/screening.yml`: `run_screening.py`를 수동 실행하고, 장부·예약 줄을 출력해 저장소에 옮겨 적는다.
4. 첫 선별: BTCUSDT 15m, 2023-04-20 ~ 2024-06-18(잠긴 구간 TEST-12·TEST-17 사이, 시작은 워밍업 약 12일이 TEST-17에 닿지 않게 ADR-0042와 같은 날), 켬/끔 4개 후보.
   이 범위 끝 20%가 BTCUSDT 결선 TEST로 예약된다.

## Consequences

- 모의투자 설정(`configs/paper.json`)과 실행 중인 후보는 바뀌지 않는다. 켜려면 선별 결과 후 결선이 필요하다.
- 선별 4개는 결선 DSR 시도 수에 더해진다(의도한 대가).
- 참고: 최대 보유 12시간(`max_hold_bars`)은 백테스트 엔진에만 있고 모의투자 엔진에는 없다. 이 ADR 범위 밖이며 따로 맞춰야 한다.

## 선별 결과 (2026-10-09, GitHub Actions run 37886908727, 선별일 뿐 검증 아님)

BTCUSDT 15m, 2023-04-20 ~ 2024-03-25(워크포워드 46폴드), TEST 2024-03-25 ~ 2024-06-18 예약. PBO 0.49, DSR 시도 수 75.

| 후보 | 폴드 평균 | 이긴 폴드 | 거래 수 | DSR |
|---|---|---|---|---|
| h16 끔 | -0.96% | 17/46 | 454 | 2e-9 |
| h16 켬(2.5 ATR) | -1.02% | 14/46 | 499 | 8e-11 |
| h48 끔 | -1.22% | 11/46 | 460 | 4e-7 |
| h48 켬(2.5 ATR) | -1.38% | 8/46 | 523 | 2e-13 |

- 네 후보 모두 손실, DSR 사실상 0. 추적 손절은 두 지평 모두 조금 더 나빴다. 거래 수가 늘었다(일찍 털려 다시 진입).
- 결론: 2.5 ATR 추적 손절을 기본으로 켜지 않는다. 결선 후보 없음. 투표 자체가 이 구간에서 비용을 넘지 못하는 것이
  먼저다(ADR-0042와 같은 방향).
- 장부·예약은 `research/screening.jsonl`, `configs/reserved_windows.json`에 옮겨 적었다(Actions 로그에서 그대로).
