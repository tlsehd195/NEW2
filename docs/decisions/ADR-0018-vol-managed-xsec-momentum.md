# ADR-0018: Volatility-managed cross-sectional momentum (H-0018)

**Status:** Accepted (H-0018 ran 2026-09-29 and failed; see Result)
**Date:** 2026-09-29
**Deciders:** account owner (chose "모멘텀 재설계" on the decision card), Claude Code session

## Context

H-0017(ADR-0017)의 21일 크로스섹션 모멘텀 롱숏은 TEST에서 +9.9%(동일가중 보유 -10.3%)로, 이 프로젝트에서 처음으로 보유를 크게 앞선 후보였다. 하지만 폴드별 성과가 들쭉날쭉해서 DSR은 0.56, 세 후보를 합친 PBO는 0.343이었다.

- 모멘텀 전략은 가끔 크게 무너지는 구간(momentum crash)이 있어 성과가 고르지 않다는 사실이 잘 알려져 있다.
- 표준 처방은 전략의 최근 변동성으로 노출을 조절하는 것이다.
  - Barroso & Santa-Clara (2015, *Journal of Financial Economics*, "Momentum has its moments")
  - Daniel & Moskowitz (2016, JFE)
- 같은 후보를 같은 데이터로 다시 돌리는 건 규칙 위반이다. 그래서 새 질문을 새 id로, 락에 걸리지 않은 새 코인·새 구간에서 검증한다.

## Decision

H-0017과 같은 21일 순위(상위 1/3 롱, 하위 1/3 숏)는 유지하고, 비중만 변동성으로 조절하는 후보 2개를 등록한다. 두 후보는 서로 다른 방식이다.

| id | 규칙 | 근거 |
|---|---|---|
| `xsec_momentum_21d_ls_invvol30_v1` | 각 다리(롱 0.5, 숏 0.5) 안에서 코인 비중을 30일 변동성의 역수에 비례하게 둔다 | 변동성이 큰 코인 하나가 스프레드를 좌우하지 않도록 한다 |
| `xsec_momentum_21d_ls_voltarget20_v1` | 동일가중 롱숏에 지난 30일 수익률을 적용해 포트폴리오 변동성을 추정하고, 연 20%가 되도록 노출을 조절한다. 총노출 상한은 2.0 | Barroso & Santa-Clara 2015 |

- 파라미터는 논문의 관례값(변동성 창 약 1개월)을 쓰고 튜닝하지 않았다. 목표 변동성 20%는 코인 롱숏 스프레드의 과거 변동성보다 낮게 잡은 보수적인 값이다.
- 후보를 2개로 줄여 시도 횟수를 늘리지 않는다. 21일 순위 자체는 H-0017에서 이미 시도로 집계됐다.
- H-0017의 21일 롱숏 원본은 이미 REJECTED이고, 새 구간에서도 다시 넣지 않는다.

### 유니버스 (`USDTM-2023`, 51개)

- `USDTM-2020`에서 BTC, ETH, XRP를 뺐다. 세 코인의 USDT-M TEST 창이 2023~2026 안에 있다.
- 2020년 중 바이낸스 USD-M에 상장된 주요 L1·DeFi 무기한 선물 30개를 더했다.
- SOL은 TEST-14가 구간 안에 있어서 뺐다.
- 밈코인은 없다.
- BTC와 ETH가 빠진 알트코인 바스켓이라는 점을 결과 해석에 적는다.

### 구간

- 2023-05-10 ~ 2026-09-01, 일봉이다.
- 시작일은 TEST-17(구 24코인, ~2023-04-06)에 워밍업 30일이 닿지 않게 잡았다.
- TEST는 마지막 20%로 약 2025-12 ~ 2026-09다.

### 등록 가능 여부

- `xsec` 계열의 두 번째 등록이다(예산: 30일에 3개).
- 죽은 계열 패턴에 해당하지 않는다.
- 새 후보 id라 동일 후보 세트 규칙에도 걸리지 않는다.

**데이터 스누핑 고지.** 이번 가설의 방향은 H-0017의 TEST 결과(롱숏 모멘텀만 보유를 이김)를 보고 정했다. 그 TEST 구간(2022-08 ~ 2023-04)과 코인은 락되어 있어서 이번 검증에 쓰지 않는다. 다만 "모멘텀 롱숏에 기대를 건다"는 선택 자체는 이미 본 결과에서 나왔다. PBO/DSR이 이 선택까지 보정해 주지는 않으므로, 통과하더라도 한 번 더 독립 구간에서 확인하기 전에는 결론으로 쓰지 않는다.

## Consequences

- 기준은 그대로다(PBO ≤ 0.2, DSR ≥ 0.95, TEST 초과수익 ≥ 0).
- 실패하면 크로스섹션 모멘텀 계열의 변형은 더 만들지 않고 방향을 바꾼다.

## First run aborted before TEST (2026-09-29)

The first H-0018 run (GitHub Actions run 36508059040) crashed during the determinism check on TRAIN+VALIDATION with `ZeroDivisionError`, before any walk-forward fold or the TEST range was evaluated.

- **Cause.** A coin in the universe had a frozen price for 30 days: an untraded or halted market, most likely a delisted one. Its volatility was zero, and the inverse-volatility weight divided by it.
- **Fixes.**
  - The engine no longer ranks a coin that did not trade in its ranking window: any zero-volume bar, or an unchanged close over the last 5 bars. These coins are counted as `ineligible_stale`, because a halted market cannot be traded (fail-closed).
  - The inverse-volatility candidate stays flat instead of raising an error.
  - The TEST range is now locked **before** the TEST run starts, not after. A crash inside TEST can no longer leave a range that was already looked at without a lock.
- **Rerun.** Nothing was observed on TEST, so the rerun uses the same H-0018 registration, content and id. The runner's first registration line was never committed, because that checkout was thrown away.

## Second run aborted before TEST (2026-09-29)

The second H-0018 run (GitHub Actions run 36510569435) also stopped during the TRAIN+VALIDATION determinism check, before any walk-forward fold or the TEST range was evaluated. The error was `funding missing for WAVESUSDT at 2025-06-19 16:00 while held`. Looking at the code turned up a second problem too.

- **A held position was stuck in a dying market.** WAVES's volume collapsed before its funding records stopped, around the delisting. The exit was capped at 5% of the day's traded value, so a remainder stayed open past the last funding record. Such a position is now closed at that bar's open, with the full uncapped impact, and counted as `funding_gap_closes`. A backtest does not stop on it any more.
- **Funding on 4h/1h contracts was undercharged.** The engine used a fixed 8h grid. Binance moved many alt contracts to 4h (some to 1h) funding, and on the 8h grid those extra settlements were silently dropped. `FundingSeries` now charges every recorded settlement at whatever interval the contract used. It counts funding as covered only when no gap between records exceeds 8h plus the stamp tolerance.
- **Rerun.** Nothing was observed on TEST, so the rerun uses the same registration and id again.

## Result (2026-09-29, third run)

H-0018 failed. PBO was 0.0, well inside the 0.2 criterion, but DSR was 0.74 for the inverse-volatility candidate and 0.43 for the volatility-target candidate, both below 0.95. Both candidates are REJECTED. TEST-18 (2026-01-02 to 2026-09-01) is locked for the basket and for all 51 coins.

- TEST returns were -15.9% and -16.4%. The equal-weight basket lost -28.0% over the same range, so the excess-return criterion passed.
- Both candidates hit the 15% drawdown halt during TEST.
- Mean fold returns were +0.7% and +0.1%, so the walk-forward shows no reliable edge.

Following the Consequences section above, no more variants of this family will be built.
