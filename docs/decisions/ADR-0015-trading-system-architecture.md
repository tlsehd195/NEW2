# ADR-0015: 스윙+스캘핑 거래 시스템 아키텍처 (데이터→피처→전략→리스크→실행→저널→연구)

**Status:** Accepted (구현됨, 기본 모드 PAPER. 라이브 연결은 사람 결정 대기)
**Date:** 2026-09-29
**Deciders:** account owner, Claude Code session

## Context

사용자(동동)는 NEW2를 연구 프로토타입에서 완성된 시스템으로 만들어 달라고 요청했다
(2026-09-28, 약 3만 자 명세). 완성된 시스템은 "데이터 수신 → 신호 → 검증 → 리스크
→ 페이퍼 실행 → 전 과정 기록"을 거치고, 검증을 통과한 후보만 사람 승인을 받아
라이브로 간다. 동시에 기존 헌법은 그대로 유지하라고 명시했다: CLAUDE.md 절대 규칙
1~5, 안전 파일, 사람만 할 수 있는 전이.

환경 제약은 다음과 같다(ADR-0012).
- 이 세션과 GitHub Actions 모두 `fapi.binance.com`/`fstream.binance.com`에 닿지
  않는다(403 / HTTP 451).
- 과거 데이터는 `data.binance.vision`으로 받을 수 있다.
- Binance API 문서도 이 세션에서는 열리지 않는다. 그래서 엔드포인트 경로는 Binance
  공식 커넥터 소스(`binance-futures-connector-python`, raw.githubusercontent.com)로
  확인했다. **응답 필드 이름은 재확인하지 못했다**(파서는 엄격하게 짜서, 필드가
  없으면 예외를 낸다).

## Decision

### 1. 하나의 경로, 세 가지 모드

```
Market Data → Feature → Regime → Signal → (Quality Gate, Kill Switch) → RiskEngine
  → OrderIntent → ExecutionEngine → Broker(Paper|Live) → Fill → Position → Exit → Outcome
```

- 백테스트(`backtest/event_engine.py`), 페이퍼(`paper/engine.py`), 라이브는 같은
  `strategies/*`, `features/*`, `risk/engine.RiskEngine`을 쓴다.
- 페이퍼와 라이브는 같은 `execution/engine.ExecutionEngine`을 쓰고, 브로커만
  `PaperBroker`/`LiveBroker`로 다르다.
- 모든 기록에는 `mode`(backtest|paper|live)가 붙는다. 그래서 백테스트 결과가
  라이브 결과로 섞일 수 없다.

### 2. 데이터 계층 (`data/`)

- `websocket.py`는 표준 라이브러리만 쓰는 RFC6455 클라이언트다(프록시 CONNECT 지원).
- `binance_ws.py`는 aggTrade/bookTicker/depth/kline/markPrice 파서다. 형식이 잘못된
  메시지는 `DataQualityEvent`로 바꾼다.
- `realtime.ResilientEventFeed`는 다음을 처리한다.
  - 지수 백오프로 재연결하고, 연속으로 실패하면 `FeedUnavailable`을 낸다.
  - 메시지가 끊기면(stale) 재연결한다.
  - 거래 id, 호가 update id, 캔들 시각으로 중복을 제거한다.
  - 역행 시각, 클럭 차이, 지연을 `DataQualityEvent`로 기록한다.
  - 캔들 공백은 REST로 채운다.
- `FeedHealthMonitor`는 심볼별 건강 상태(`HealthStatus`)를 알려준다.
- `orderbook.LocalOrderBook`은 스냅샷과 델타를 동기화한다(U/u/pu 연속성 검사).
- `quality_gate.evaluate_data_quality`는 진입 전 fail-closed 게이트다.
- 시간 단위는 1m/3m/5m/15m/1h/4h/1d를 유지한다.

### 3. 피처와 레짐 (`features/`)

- EMA, RSI, ATR은 고정 창(4×period)으로 계산한다. 그래서 워밍업 길이와 관계없이
  같은 값이 나오고, 라이브와 백테스트 결과가 같다.
- 모든 피처는 해당 시점까지 마감된 봉만 쓴다(무결성 검사가 확인한다:
  `validation/integrity.py`).
- 레짐은 HIGH_VOL > TREND_UP/DOWN > LOW_VOL > RANGE 순서로 판정한다.
  UNDEFINED이면 진입을 차단한다.
- 기록에는 `FEATURE_VERSION`이 남는다.

### 4. 전략 (`strategies/swing.py`, `scalp.py`, `registry.py`)

- 전략은 상태를 갖지 않는다. `Signal`만 내고, 진입 신호에는 손절 거리가 반드시
  있어야 한다.
- 스윙 후보는 추세, 돌파+거래량, 볼린저 평균회귀, 레짐 하이브리드다.
- 스캘핑 후보는 VWAP 회귀, 레인지 돌파+거래량, 단기 평균회귀, 미시구조 모멘텀이다.
- 레지스트리(`configs/strategies.json`)는 id, 버전, 워밍업을 검증한다. 등록하지
  않은 버전은 실행할 수 없다.
- 죽은 계열은 `research/hypotheses.DEAD_CANDIDATE_PATTERNS`로 재등록을 막는다.
  2/14/28 모멘텀 그리드와 3/9/21 펀딩 캐리 그리드가 여기에 해당한다.
- 모든 후보는 **CANDIDATE**다. 검증된 우위는 없다.

### 5. 리스크 (`risk/engine.py`)

하나의 `RiskEngine`이 다음을 전부 판정하고, 결정마다 `decision_id`와
`config_version`을 남긴다.
- 거래당 위험 기반 사이징
- 레버리지 상한, 포지션 상한
- 일손실, 최대 낙폭
- 연속 손실, 쿨다운(`risk/protections.py` 연결)
- 변동성 킬(ATR/가격)
- 스프레드, 피드 상태, 데이터 품질, 레짐, 킬 스위치
- 거래소 규칙(틱, 스텝, 최소 수량, 최소 명목가치)

### 6. 백테스트 (`backtest/event_engine.py`)

- 다음 봉에서 체결하고, 레이턴시는 1봉 이상이다.
- 비용은 테이커/메이커 수수료, 스프레드, 참여율 기반 충격 비용이다.
- 지정가는 가격을 관통해야 체결되고, 부분 체결과 미체결이 있다.
- 펀딩 시계열이 비면 예외를 낸다(명시적으로 끈 경우만 가정으로 보고한다).
- 격리마진 청산, 손절/익절/트레일링을 반영한다(같은 봉에서는 손절을 먼저 가정).
- 비용 분해는 Gross − Fees − Spread − Slippage − Funding = Net이다.
- MFE, MAE, 청산 사유를 기록한다.

### 7. 검증 (`validation/signal_study.py`, `integrity.py`)

- 순서는 사전등록 → 가설 검사(죽은 계열, 30일당 계열 예산, 락 구간) → 워크포워드와
  무결성(미래참조, 워밍업 민감도, 결정성) → PBO/DSR → TEST 1회 → 즉시
  `lock_test_window`다.
- 자동 승격은 OOS_TESTED까지만 한다(`research/lifecycle.CandidateLedger`).
  APPROVED/DEPLOYED로 가려 하면 `PermissionError`가 난다.

### 8. 실행 (`execution/`)

- `OrderIntent`는 결정적인 `client_order_id`를 쓴다(sha256).
  - 진입에는 `risk_decision_id`가 필수다.
  - 청산, 손절, 익절은 `reduce_only`가 필수다.
- `ExecutionEngine`은 전송 전에 `PENDING_SUBMIT`를 기록하고, 같은 id는 다시
  보내지 않는다.
- 전송 결과를 모르면 `UNKNOWN`으로 두고 조회한다. 조회도 실패하면 모든 신규 주문을
  차단한다.
- 재조정(`reconciliation.py`)에서 불일치가 나오면 차단하고 CRITICAL로 알린다.
  **자동 수정은 절대 하지 않는다.**
- `PaperBroker`는 실제 호가와 체결 데이터로 체결을 시뮬레이션한다(레이턴시, IOC
  부분 체결, 관통 체결, 펀딩). 상태는 저장 후 복원된다.
- `binance_client.BinanceFuturesClient`는 공개/서명 조회를 담당한다. 주문을
  바꾸는 `_new_order`/`_cancel_order`는 `LiveBroker`에서만 호출할 수 있다(AST
  테스트).
- `LiveBroker`는 전송 직전마다 보호 파일 `live/safety_gate.evaluate_safety_gate`를
  새로 평가한다.

### 9. 페이퍼 트레이더 (`paper/engine.py`, `scripts/run_paper_trader.py`)

- 상시 실행된다. 상태는 원자적 JSON(`os.replace`)과 추가 전용 주문 로그로 남긴다.
- 복구 순서는 로컬 상태 복원 → 브로커 상태 → 계좌 → 미체결 주문 → 포지션 → 재조정
  → 진입 허용이다. 복구 전에 받은 이벤트로는 아무것도 결정하거나 저장하지 않는다.
- 진입이 체결되면 즉시 보호용 STOP_MARKET을 건다. 걸지 못하면 곧바로 청산한다.
- 익절과 트레일링은 호가 갱신 때마다 관리한다.
- 페이퍼 킬 스위치는 라이브와 별개 파일이다. **파일이 없으면 페이퍼에 한해 "해제"로
  본다**(돈이 걸리지 않기 때문). 파일이 있고 engaged이거나, 읽을 수 없으면 진입을
  차단한다. 최대 낙폭에서 자동으로 engage하고, 해제는 사람만 한다. 라이브는 기존
  규칙(파일 없음 = engaged)을 그대로 쓴다.

### 10. 계층형 저널과 데이터 보존 (`journal/`)

- 계층은 raw → normalized → feature → decision → execution → outcome, 그리고
  quality, safety, audit이다. `<layer>/<YYYY-MM-DD>.jsonl`에 추가 전용으로 쓴다.
- 기록마다 `data_schema_version`을 남기고, 필수 필드가 빠진 기록은 거부한다.
- 체인은 id로 연결된다: `decision_id` → `risk_decision_id` → `client_order_id` →
  fill → outcome(`entry_decision_id`).
- 대용량 raw 데이터는 분 단위 통계로 줄여서 영구 보존한다
  (`aggregation.MinuteTradeAggregator`, `MinuteBookAggregator`).
- `configs/retention.json`의 규칙은 이렇다.
  - raw만 삭제할 수 있다(14일). 나머지 계층은 **보호되어 삭제할 수 없고, 설정
    로더가 거부한다**.
  - 지난 파티션은 gzip으로 압축한다(원본과 같은지 확인한 뒤 교체).
- `storage_report`는 저장 용량을 감시하고 경고 또는 CRITICAL로 알린다.
- `scripts/run_maintenance.py`는 한 번에 처리하는 파일 수에 상한이 있고, 오늘
  파티션은 건드리지 않는다. 그래서 거래를 막지 않는다.
- DB는 도입하지 않았다. `src/`는 표준 라이브러리만 쓰고, 기존 JSONL 방식을
  확장했다.

### 11. 연구 데이터셋 (`research/dataset.py`)

- 타깃은 미래 수익률, MFE, MAE, success이다. 타깃에는 피처 봉 이후의 봉만 쓴다.
- 누수(피처 as_of가 행 시각보다 늦음)는 제외한다.
- 락 구간에 닿는 행은 제외한다.
- 생존 편향은 조기 종료 심볼로 보고한다.
- 선택 편향은 제외 사유별 개수로 보고한다.
- 저널/데이터셋 코드는 `configs/`나 전략 레지스트리를 쓰지 않는다(AST 테스트).
  **축적된 데이터가 라이브 전략을 자동으로 바꾸는 일은 없다.**

### 12. 알림 (`notifications/notifier.py`)

- 등급은 INFO/TRADE/WARNING/CRITICAL이다.
- 웹훅 URL, API 키/시크릿, 긴 토큰을 가린다(redact).
- 같은 알림은 속도를 제한한다(CRITICAL 제외).
- 전송에 실패해도 거래 루프는 멈추지 않고, 실패는 기록된다.

## 사람 결정으로 남긴 것 (의도적으로 구현하지 않음)

1. **라이브 연결.** 실행 중인 프로세스가 사람의 `LiveActivationApproval`을 읽어 들이는
   경로는 만들지 않았다. `payload_to_approval`은 어디서도 호출하지 않는다는 기존 AST
   규칙을 유지했다. `LiveBroker`를 만드는 코드도 `src/`나 `scripts/`에 없다(AST
   테스트). 이 경로를 어떻게 열지(승인 파일의 위치, 만료, 누가 서명하는지)는 안전
   경계 결정이라 계좌 소유자가 정해야 한다.
2. **라이브 인프라.** Binance가 미국 리전에서 451을 돌려주는 문제(ADR-0012)가 아직
   남아 있다.
3. **페이퍼 킬 스위치 해제.** 스크립트를 만들지 않았다(해제는 사람만 한다).

## Consequences

- 긍정:
  - 페이퍼에서 기록되는 모든 거래를 피처, 레짐, 신호, 리스크 결정, 주문, 체결까지
    거슬러 추적할 수 있다.
  - 백테스트, 페이퍼, 라이브가 같은 리스크 판단과 같은 기록 형식을 쓴다.
  - 24시간, 7일, 30일 모의 실행 테스트에서 메모리 상한, 중복 없음, 일별 증가량이
    일정함을 확인했다.
- 부정/위험:
  - `configs/markets.json`의 거래소 규칙과 Binance 응답 필드는 미확인이다. 라이브
    전에 `exchangeInfo`로 갱신하고 실제 응답으로 파서를 확인해야 한다.
  - 페이퍼 체결은 베스트 호가 수량까지만 채운다(depth 스트림 동기화는 페이퍼
    트레이더에 아직 연결하지 않음). 보수적이지만 실제와 다를 수 있다.
  - 전략 후보는 전부 실데이터 검증 전이다(INCONCLUSIVE 이전 단계).
