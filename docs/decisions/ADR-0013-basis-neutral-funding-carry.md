# ADR-0013: Basis-neutral funding carry (hedged spot+futures)

**Status:** Accepted (data source confirmed 2026-09-28, implementation done; H-0012 real-data validation still pending)
**Date:** 2026-09-28
**Deciders:** account owner, Claude Code session

## Context

H-0011(ADR-0009 3-후보 그리드, TEST-11)은 사전등록 기준을 전부 미달로 실패했다
(PBO 0.943, DSR 전부 ~0, TEST 전부 손실). 하지만 `test_funding_only_return`(펀딩
PnL만 분리한 값)은 3개 후보 전부 미미하게 플러스(+0.02%~+0.44%)였다 — 손실은
거의 전부 `funding_carry_engine.py`가 그대로 노출하는 **방향성 가격 리스크**에서
왔다. ADR-0009는 "숏 또는 롱 단일 방향 익스포저로 펀딩을 수취"하는 설계였는데,
이건 원 논문(Management Science, "Crypto Carry")이 실제로 검증한 구조와 다르다 —
그 논문은 현물을 동시에 반대 방향으로 들어 가격 방향을 상쇄시키는 현물-선물
베이시스(캐리) 거래를 검증했고, 방향성 베팅이 아니다.

지금까지 이 프로젝트가 쓴 모든 데이터는 바이낸스 **선물** 하나뿐이다
(`binance_futures.py`/`binance_vision.py`의 `BinanceVisionFuturesCandles`). 현물
가격 시계열은 전혀 없다(업비트 KRW-BTC는 원화 표시라 바이낸스 USDT 선물과 통화가
달라 베이시스 계산에 못 쓴다). 그래서 진짜 헤지 손익을 측정하려면 바이낸스
**현물** USDT 페어 과거 데이터가 새로 필요하다.

ADR-0012에서 확인한 대로 `fapi.binance.com`(선물 실시간 API)는 GitHub Actions에서
451이지만 `data.binance.vision`(정적 아카이브)은 막혀있지 않다. 이 아카이브는
선물뿐 아니라 현물(`data/spot/...`) 히스토리도 같은 방식(월간 zip, 트레일링
부분월은 일간 zip)으로 공개한다 — 별도 API 호출이 아니라 정적 파일이므로 같은
경로로 우회될 것으로 예상하지만, 실제로 GitHub Actions 러너에서 200이 오는지는
구현 직후 반드시 실제 요청으로 확인한다(CLAUDE.md 규칙 4/5, 가정으로 넘기지
않는다).

## Decision

1. **`BinanceVisionSpotCandles`를 `binance_vision.py`에 추가한다.** 기존
   `BinanceVisionFuturesCandles`와 동일한 월간/일간 아카이브 전략, 경로만
   `data/spot/...`로 바꾼다. 실제 GitHub Actions 요청으로 200 응답과 CSV 스키마를
   확인하기 전에는 어떤 검증에도 쓰지 않는다.
2. **새 백테스트 엔진 `backtest/basis_carry_engine.py`를 추가한다.** 기존
   `funding_carry_engine.py`(단일 방향 익스포저)는 건드리지 않는다 — H-0011 결과와
   비교 가능하도록 그대로 둔다. 새 엔진은 매 펀딩 정산마다:
   - 목표 헤지 비율(0 = 캐리 안 함, 1 = 최대 캐리)을 받는
     `BasisCarryStrategy(spot_history, futures_history, funding_history) -> float`
     프로토콜을 쓴다.
   - 캐리 온(비율 f>0)일 때: 숏 선물 명목가치 = 롱 현물 명목가치 = f * equity
     (델타 중립, 펀딩비 부호가 지속적으로 양수일 때만 이 방향이 캐리; 지속적으로
     음수면 반대로 롱 선물/숏 현물 — 전략이 결정).
   - **베이시스 손익은 실측한다(가정하지 않는다):** 각 구간 손익 = 현물 수익률 -
     선물 수익률(두 실제 시계열의 실제 변화, 완전한 상쇄를 가정하지 않음) + 펀딩
     손익 - 양쪽 다리 체결비용. 이게 ADR-0009 설계와의 핵심 차이 — 방향성 리스크를
     "가정으로 0"이 아니라 "실제 두 시계열 차이"로 측정해서, 베이시스 자체가
     확대/축소되는 리스크까지 결과에 남긴다(CLAUDE.md 규칙 5: 출처/실측 숨기지
     않는다).
   - 유동성 캡은 두 다리 모두에 독립적으로 적용한다(각 다리의 `max_participation`을
     각자의 시장 depth 대용치로 넘지 않게) — `funding_carry_engine.py`와 동일한
     `liquidity_capped_settlements` 패턴, 다리별로 따로 카운트.
   - 한쪽 다리(현물 또는 선물)의 마크 가격을 못 구하면 fail-closed: 그 정산을
     `gaps`로 기록하고 두 다리 모두 포지션을 0으로 되돌린다(한쪽만 청산돼서
     델타 중립이 깨진 채로 남는 걸 방지).
3. **워크포워드/PBO/DSR/사전등록 절차는 기존과 완전히 동일하게 적용한다.** 새
   가설 ID(H-0012)로 새로 등록하고, `funding_study.py`와 같은 순서
   (walk-forward → PBO/DSR(프로젝트 전체 후보 수로 디플레이트) → held-out TEST 1회
   → 즉시 락)를 따르는 `basis_carry_study.py`를 만든다. 기존 `funding_study.py`는
   건드리지 않는다.
4. **1차 후보는 단순 부호 규칙으로 시작한다**: 최근 N개 정산의 펀딩비 평균 부호로
   캐리 온/오프와 방향을 정하는 후보 그리드(예: N=3/9/21, H-0011과 비교 가능하도록
   같은 lookback 그리드를 재사용하되 이번엔 헤지가 걸려 있음). 횡단면(여러 자산
   동시) 캐리는 여전히 범위 밖(ADR-0009 결정 5 유지).
5. **레버리지/청산은 이번에도 범위 밖이다** — `funding_carry_engine.py`와 동일한
   스코프 갭(exposure를 레버리지 배수 없이 equity의 비율로만 다룸)을 그대로
   유지하고 문서에 명시한다.

## Consequences

- **확인 완료 (2026-09-28)**: `data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/...`가
  실제 GitHub Actions 러너에서 HTTP 200을 반환하고, CSV 스키마가 선물 klines와
  완전히 동일함을 실제 요청으로 확인했다(런
  https://github.com/tlsehd195/NEW2/actions/runs/36410099639). 이 ADR의 데이터
  소스 가정은 유효하다.
- `BinanceVisionSpotCandles`, `basis_carry_engine.py`,
  `strategies/basis_carry.py`(후보 그리드), `validation/basis_carry_study.py`,
  실행 스크립트 `scripts/run_basis_carry_study.py` +
  `.github/workflows/basis_carry_study.yml`를 구현했다. 유닛 테스트로 실측
  베이시스 수익률 계산(완전 동일 이동=0, 다른 이동=실측 차이), 양다리 유동성 캡,
  한쪽 다리 마크 누락 시 fail-closed 정지를 확인했다(실네트워크 없이, 가짜
  데이터로).
- 기존 `funding_carry_engine.py`/H-0011 결과/TEST-11 락은 전혀 영향받지 않는다
  (완전히 별도 경로, 같은 `market="BTCUSDT"` 문자열을 쓰지만 새 엔진이라 locked
  window 충돌 여부는 walk-forward 구간이 겹치는지로만 판단하면 된다).
- 이번에도 결과가 사전등록 기준을 넘는다는 보장은 없다 — 베이시스 자체가
  변동하면(스퀴즈, 디페그 등) 손실이 날 수 있고, 이 역시 실제 데이터로만 확인
  가능하다. 구현 전까지 이 방향은 미검증 상태.
- 현물 아카이브 경로가 실제로 GitHub Actions에서 막혀 있는 것으로 확인되면(선물과
  달리 차단될 가능성도 있음), 이 ADR의 데이터 소스 가정은 무효가 되고 다른 경로를
  다시 찾아야 한다 — 확인 결과는 구현 PR에 남긴다.
