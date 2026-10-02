# ADR-0009: 펀딩레이트 캐리 전략을 워크포워드 파이프라인에 통합하는 설계

**Status:** Proposed
**Date:** 2026-09-28
**Deciders:** account owner, Claude Code session

## Context

ADR-0002/0004에서 바이낸스 선물 청산가·펀딩비 계산은 끝났고, `binance_funding.py`
(펀딩비 이력, 공개 REST)와 `binance_futures.py`(선물 캔들, 공개 REST) 데이터 소스도
있다(2026-09-28 추가). 다만 이 둘을 실제 전략으로 쓰는 코드는 아직 없다. 지금까지의
모든 가설(H-0001~H-0009)은 가격만 보는 모멘텀 계열이라, 일봉 모멘텀의 PBO가
0.3대에서 정체된 상태(ADR-0007/H-0007/H-0008)이고 이 신호 계열만으로 사전등록 기준
(PBO≤0.2, DSR≥0.95)을 넘을지 불확실하다. 펀딩레이트 캐리(참고: Management Science
"Crypto Carry" 계열 논문, [[new2-paper-graded-strategies]])는 가격이 아니라 펀딩비
부호/크기를 신호로 쓰는, 지금까지의 모멘텀과 상관이 낮을 가능성이 있는 별도 edge
후보다.

현재 `backtest/engine.py`의 `run_backtest()`와 `validation/study.py`의 `run_study()`는
캔들 시퀀스 하나만 받는 `Strategy` 프로토콜(`__call__(history: Sequence[Candle]) ->
float`)을 전제로 설계돼 있다. 캐리 전략은 캔들뿐 아니라 정산 시점별 펀딩비 이력도
같이 봐야 한다 — 이는 기존 엔진이 지원하지 않는 두 번째 시계열 입력이라, 기존
엔진에 파라미터를 몰래 추가하는 대신 별도 설계가 필요하다. 이번 세션에서는 설계만
정하고 구현/검증은 다음 단계로 미룬다 (CLAUDE.md: 절차를 건너뛰지 않고, 미완성
구현을 만들지 않는다).

## Decision

1. **새 프로토콜 `FundingAwareStrategy`를 추가한다** (`strategies/`에 위치, 기존
   `Strategy`와 별개): `__call__(price_history: Sequence[Candle], funding_history:
   Sequence[FundingRateRecord]) -> float`. 기존 `Strategy`를 쓰는 모멘텀류는 전혀
   건드리지 않는다.
2. **새 백테스트 경로 `backtest/funding_carry_engine.py`를 추가한다.** 기존
   `run_backtest()`를 수정하지 않고 별도 함수로 만든다: 캔들과 펀딩비 이력을 시간
   정렬해서 각 펀딩 정산 시점(8시간 주기)마다 전략을 호출해 포지션을 정하고,
   `backtest/funding.py`의 `apply_funding_payment()`로 실제 정산 손익을 반영한다.
   가격 변동에 의한 손익(마크투마켓)과 펀딩 손익을 분리해서 리포트한다.
3. **펀딩비 데이터가 비는 구간(gap)은 fail-closed로 처리한다** (CLAUDE.md 규칙 4):
   해당 정산 시점을 건너뛰지 않고, 포지션을 0으로 강제하거나 백테스트를 그 구간에서
   중단하고 이유를 리포트에 남긴다 — 데이터가 없는데 펀딩비를 0으로 가정하지
   않는다.
4. **워크포워드/PBO/DSR/사전등록 절차는 기존과 동일하게 적용한다.** 캐리 전략도
   다른 가설과 똑같이 `preregistration.py` → locked window 확인 → walk-forward →
   PBO/DSR → held-out TEST 순서를 거치며, 새 프로토콜이라고 절차를 건너뛰지 않는다.
5. **1차 후보 신호는 단일 자산 시계열 캐리로 시작한다**: 펀딩비가 지속적으로
   양수(롱이 숏에 지불)면 숏 포지션으로 펀딩을 수취하고, 지속적으로 음수면 롱
   포지션을 취하는 단순 규칙. Management Science 논문들이 다루는 횡단면(여러 자산
   동시 롱/숏) 캐리는 범위 밖으로 미룬다 — 이 프로젝트는 아직 한 번에 한 자산만
   본다.

## Consequences

- 다음 세션(또는 이 세션의 후속 작업)에서 `FundingAwareStrategy` 프로토콜,
  `funding_carry_engine.py`, 최소 1개 캐리 후보(예: `FundingCarry(threshold=...)`),
  그리고 이를 실행하는 워크포워드 스크립트/워크플로 입력을 실제로 구현해야 한다 —
  이 ADR 자체는 코드를 추가하지 않는다.
- 기존 `run_backtest()`/`Strategy`/모멘텀 후보/락된 TEST 윈도우는 전혀 영향받지
  않는다 (완전히 별도 경로).
- 펀딩비 데이터는 바이낸스 선물(USDT-M)에만 있으므로, 캐리 전략의 `market` 문자열은
  업비트 KRW 계열과 겹치지 않는 바이낸스 심볼(예: "BTCUSDT")을 쓴다 — locked window
  충돌 없음.
- 구현되기 전까지 이 방향은 여전히 미검증 상태이며, 모멘텀 계열과 마찬가지로
  사전등록 기준을 통과하기 전에는 실거래 근거가 될 수 없다.
