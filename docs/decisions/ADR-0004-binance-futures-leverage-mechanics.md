# ADR-0004: 선물 레버리지: 청산가·펀딩비 계산 근거

**Status:** Accepted
**Date:** 2026-09-28
**Deciders:** account owner (동동), Claude Code session

## Context

ADR-0002에서 바이낸스 선물/레버리지로 매매 거래소를 정했지만, 청산가·마진·펀딩비
계산은 구현을 미뤘다. 동동님이 "이거 논문 기반으로 만드는 거 어때?"라고 물었다
(2026-09-28).

확인 결과, 청산가·펀딩비 계산식은 학술 연구 주제가 아니라 **바이낸스가 약관으로
정의한 계약 규칙**이다. 논문은 존재하지 않거나(청산가는 거래소마다 다른 자체
규칙) 무관하다. 따라서 1차 소스로 바이낸스 공식 문서를 쓴다:

- 청산가: [How to Calculate Liquidation Price of USDⓈ-M Futures Contracts](https://www.binance.com/en/support/faq/how-to-calculate-liquidation-price-of-usd%E2%93%A2-m-futures-contracts-b3c689c1f50a44cabb3a84e663b81d93?hl=en)
- 펀딩비: [Introduction to Binance Futures Funding Rates](https://www.binance.com/en/support/faq/360033525031)

이 페이지들은 핵심 관계식을 텍스트로 명시한다:

- `Maintenance Margin = Position Notional * Maintenance Margin Rate - Maintenance Amount`
  (티어별 MMR·Maintenance Amount, 티어는 포지션 명목가치 구간별로 다름)
- 격리(isolated) 마진에서는 `WB = isolatedWalletBalance`, `TMM = 0`, `UPNL(다른 포지션) = 0`
  으로 치환한다.
- `Funding Amount = Nominal Value of Positions * Funding Rate`; 양의 펀딩비면
  롱이 숏에게 지불, 음의 펀딩비면 숏이 롱에게 지불. 기본 정산 주기 8시간.

바이낸스는 정확한 청산가 대수식 자체는 이미지로만 제공해서(텍스트로 긁을 수
없음), 위 관계식으로부터 직접 유도했다 — 이 세션이 재구성한 식이지 바이낸스
페이지를 그대로 복사한 게 아니다. 격리 마진·단방향 포지션 기준, 청산 시점에
`WB + UPNL = Maintenance Margin`이 성립한다고 놓고 풀면:

- 롱: `Liquidation Price = (Entry Price * Position Size - WB - MaintAmount) / (Position Size * (1 - MMR))`
- 숏: `Liquidation Price = (Entry Price * Position Size + WB + MaintAmount) / (Position Size * (1 + MMR))`

## Decision

1. **마진 등급표(MMR·Maintenance Amount 티어)는 코드에 하드코딩하지 않는다.** 심볼별로
   다르고 바이낸스가 수시로 바꾼다. `risk/leverage.py`는 `MarginTier` 목록을
   호출하는 쪽이 넘기도록 하고, 비어 있거나 포지션 명목가치를 못 덮으면 fail-closed로
   예외를 낸다 — 오래된 값을 몰래 쓰지 않는다.
2. **청산가 계산은 격리 마진·단방향(one-way) 포지션만 지원한다.** 교차 마진(cross
   margin, 다른 포지션의 TMM/UPNL을 합산)은 훨씬 복잡하고 이 프로젝트가 아직 여러
   포지션을 동시에 열 계획이 없어 범위 밖이다. 필요해지면 별도 ADR로 확장한다.
3. **펀딩비는 백테스트에 실제 과거 펀딩비 시계열이 있을 때만 반영한다.** 이 세션은
   바이낸스 펀딩비 API에 접근할 수 없어(네트워크 차단) 과거 펀딩비를 대신 추정하지
   않는다 — 프리미엄 인덱스에서 펀딩비를 재계산하려면 impact bid/ask 등 별도
   데이터가 필요한데, 이는 근사가 아니라 실제로 없는 데이터라서 fail-closed가
   맞다. `backtest/funding.py`는 봉마다 실제 펀딩비 값을 받아야 하고, 없으면
   0으로 가정하지 않고 예외를 낸다.
4. **청산 체크는 백테스트 봉의 저가/고가로 한다.** 봉 내 어느 시점이든 청산가를
   건드리면 해당 봉에서 강제청산된 것으로 처리하고, 그 이후 봉은 거래하지 않는다
   (증거금 전액 손실, 재진입은 별도 신규 포지션).
5. **`live/kill_switch.py`는 이 작업에서 건드리지 않는다.** 마진비율 기반 자동
   킬 스위치 트리거는 보호 파일 변경이라 동동님의 명시적 확인이 있을 때 별도로
   진행한다.

## Consequences

- `risk/leverage.py`: `MarginTier`, `PositionSide`, `find_maintenance_tier`,
  `estimate_liquidation_price` — 위 유도식 구현, 모든 입력에 fail-closed 가드.
- `risk/futures_sizing.py`: `size_futures_position` — 레버리지 상한, 청산가까지
  최소 여유(buffer) 미달 시 거부.
- `backtest/funding.py`: `apply_funding_payment` — 부호 규약(양의 펀딩비 + 롱 =
  비용) 구현.
- `backtest/futures_engine.py`: 기존 `backtest/engine.py`를 레버리지·청산·펀딩비를
  반영하도록 확장한 별도 엔진(기존 현물 엔진은 그대로 둔다).
- 이후 전략 후보 자체(추세추종 외 신호)를 넓힐 때는 논문 기반 서베이를 별도로
  진행한다 — 이 ADR의 범위 밖.
