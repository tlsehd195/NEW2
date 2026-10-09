# ADR-0055: 페이퍼 엔진: 시간손절·일일 진입 상한·호가 깊이 연결

**Status:** Accepted
**Date:** 2026-10-10
**Deciders:** account owner (분석 보고서 Phase 1 진행 지시), Claude Code session

## Context

외부 분석 보고서가 모의투자 엔진의 결함 4건을 지적했다. 코드로 하나씩 확인했다.

1. **사실.** `max_hold_bars`(DayTradeVote 48봉 = 12시간)와 `max_entries_per_day`(100)는 `backtest/event_engine.py`만 강제했다. `paper/engine.py`는 `bars_held`를 올리기만 했다. 검증된 백테스트 동작과 모의투자 동작이 달랐다(ADR-0052 상태 문서에도 같은 메모가 있었다).
2. **사실, 단 보고서의 처방은 부족.** 소켓은 `@depth@100ms`를 구독하고 `DepthDelta`까지 파싱하지만 `PaperTrader.process()`가 처리하지 않아 `PaperBroker.on_depth`와 `LocalOrderBook`이 한 번도 불리지 않았다(시장가는 항상 최우선 호가 IOC). 다만 `LocalOrderBook`은 델타만으로는 동기화되지 않는다. REST 스냅샷(`/fapi/v1/depth`)이 필요하다. 분기만 추가하면 책이 영원히 버퍼 상태다.
3. **사실 아님.** "워크플로우 3개의 `branches: ain]` 오타"는 현재 main에 없다. 세 파일 모두 `branches: [main]`이다. 고치지 않았다.
4. **사실.** `risk/margin_policy.py` 머리 설명이 "진입 경로에 아직 연결 안 됨"이라 했으나 모의투자 경로에는 ADR-0038로 연결돼 있다(라이브는 미연결).

## Decision

**시간손절.** 전략이 `max_hold_bars`를 가지면(없으면 끔, 백테스트와 같은 `getattr`) 모의투자 `_evaluate`에서 `bars_held >= max_hold_bars`이고 포지션이 `open`이며 재시도 대기 청산이 없을 때 시장가 청산한다. 청산 사유는 한국어 `시간손절(12시간)`(봉 길이 × 봉 수로 계산). 신호 청산이 같은 봉에 있으면 신호 청산이 우선한다. 청산 주문이 실패하면 기존 `pending_exit` 재시도 경로를 탄다.

**일일 진입 상한.** 전략의 `max_entries_per_day`(없으면 끔)를 UTC 날짜별로 (전략, 심볼) 단위로 센다. 상한에 닿으면 리스크 평가 전에 `blocked`, 이유 `entry_cap_per_day`(백테스트 집계 키와 같음). 진입 주문이 접수될 때 센다. 카운터는 `paper_state.json`의 `entries_by_day`에 저장되어 재시작 후에도 유지된다(키가 없는 옛 상태 파일은 0으로 읽으므로 `STATE_VERSION`은 그대로).

**백테스트와의 의미 차이(알고 둠).** 백테스트는 "체결 봉 기준 경과 봉 수 >= 한도"를 닫힌 봉에서 판단하고 다음 봉 시가에 체결한다(지연 1봉). 모의투자는 진입 후 닫힌 봉을 세어 한도에 닿은 봉의 종가 직후 청산한다. 두 쪽 모두 지연 1봉 이내이며 `holding_bars`는 모의투자가 한도와 같다(백테스트는 한도 + 0~1).

**호가 깊이.** `process()`에 `DepthDelta` 분기를 넣는다. `PaperTrader(depth_snapshot=...)`로 공개 REST 스냅샷 함수(`BinanceFuturesClient().depth_snapshot`, 키 없음)를 받아 심볼별 `LocalOrderBook`을 만들고, 동기화되면 상위 20호가를 최대 200ms에 한 번 `PaperBroker.on_depth`로 넘긴다. 스냅샷 실패는 `audit`에 남기고 30초 뒤 다시 시도하며 루프는 멈추지 않는다. 델타는 저널에 쓰지 않는다. 시퀀스 갭만 `quality` 레이어에 `blocks_trading: false`로 기록한다(깊이를 잃으면 최우선 호가 체결로 되돌아갈 뿐 거래를 막을 이유가 아니다). `depth_snapshot`이 없으면(리플레이, 테스트 기본값) 이전과 똑같이 동작한다. `scripts/run_paper_trader.py`가 라이브 실행에서만 연결한다.

**주석.** `margin_policy.py`의 머리 설명을 사실대로 고쳤다.

## Consequences

- 모의투자 체결·청산 동작이 바뀐다. 12시간을 넘긴 포지션은 이제 닫히고, 시장가 체결은 최우선 호가 수량을 넘는 주문에서 더 불리한 평균가가 나온다(더 현실적). 이전 모의투자 성과와 직접 비교하지 말 것.
- 설정 파일(`markets.json`, `margin_policy.json`, `risk.json`)·가설·예산은 건드리지 않았다.
- **실행 중인 모의투자는 재시작해야 새 코드를 받는다.** 재시작 시 저장된 열린 포지션의 `bars_held`가 이미 한도를 넘었으면 다음 봉에서 바로 시간손절된다.
- 시작/재연결 때 스냅샷 REST 호출이 심볼당 한 번 생긴다(가중치 20, 30초 이상 간격). 이 호출은 이벤트 루프에서 동기로 돈다.
- 남은 일: 실제 WebSocket 메시지로 깊이 동기화가 맞는지는 실전 스트림에서 확인해야 한다(`check_ws_quality.py` 런북).
