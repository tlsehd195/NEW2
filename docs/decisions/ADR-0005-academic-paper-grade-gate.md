# ADR-0005: 전략 후보 문헌 근거: S/A/B/C 논문 등급, S등급만 채택

**Status:** Accepted
**Date:** 2026-09-28
**Deciders:** account owner (동동), Claude Code session

## Context

ADR-0004 이후 동동님이 전략 후보(추세추종 외)를 논문 기반으로 넓히자고 했고,
"논문을 S A B C로 나눠서 등급 나눠서 S등급 위주로만 모아서 진행"이라고 요청했다
(2026-09-28).

## Decision

### 등급 기준

- **S**: 최상위 피어리뷰 저널(Review of Financial Studies, Journal of Finance,
  Management Science 등 top-5/top-tier 파이낸스·경영과학 저널)에 게재. 크립토
  시장 자체를 다룬다(주식/FX 결과를 유추한 게 아님). 방법론(형성기간·보유기간·
  유의성 검정)이 논문에 명시돼 코드로 재현 가능.
- **A**: 중앙은행/국제기구 워킹페이퍼(BIS 등) 또는 저명 저자의 SSRN/arXiv
  프리프린트로 피어리뷰 절차 중이거나 게재 전. 방법론은 탄탄하지만 아직
  저널 검증을 거치지 않음.
- **B**: 대학 워킹페이퍼·학위논문, 게재 저널이 불명확하거나 인용이 적음.
- **C**: 블로그, 거래소 마케팅 자료, 피어리뷰 없음, 비용모델 없는 순수 백테스트.

이번 라운드는 **S등급만** 채택한다(동동님 지시).

### 채택한 S등급 논문

| 논문 | 저널 | 핵심 결과 |
| --- | --- | --- |
| Liu & Tsyvinski (2021), "Risks and Returns of Cryptocurrency" | Review of Financial Studies | BTC 1주 모멘텀: 이번주 수익률 1표준편차 상승 → 다음주 수익률 +3.16%p. 1~4주 지평에서 유의. 상위/하위 quintile 스프레드 주당 8.62%p. ETH/XRP도 일부 지평에서 유의. |
| Liu, Tsyvinski & Wu (2022), "Common Risk Factors in Cryptocurrency" | Journal of Finance | 1~4주 형성기간·1주 보유의 모멘텀 팩터(CMOM)가 주당 2.5~4.1% 초과수익, t값 2.0~2.7로 유의. 시장·사이즈 팩터로 설명되지 않음. |
| "Crypto Carry" | Management Science | 선물-현물 베이시스(펀딩비와 직결)가 연 40%까지 벌어지는 시기가 있고 시간에 따라 크게 변동 — ADR-0004의 펀딩비 백테스트 엔진과 바로 연결됨. |

(검색·본문 확인 기록: NBER 워킹페이퍼 버전 `w24877`/`w25882`에서 정량적 수치 확인,
Management Science는 초록만 확인 — 유료 논문 본문은 이 세션이 접근 못 함, 후속
검증 필요.)

### 이번에 채택하지 않음 (A/B/C, 참고용으로만 기록)

- BIS 워킹페이퍼 "Crypto carry"(같은 저자/주제, A등급 후보) — Management Science
  게재판이 이미 S등급으로 있어 중복 채택 안 함.
- "Dynamic time series momentum of cryptocurrencies" (Elsevier 저널, 정확한 저널
  등급 미확인) — B등급 보류, 필요시 재평가.
- SSRN 통화(FX) 모멘텀 논문, 거래소/블로그 자료 — 크립토 특화 아니거나 피어리뷰
  없어 C등급, 채택 안 함.

### 코드 반영

1. `strategies/baselines.py`에 `TimeSeriesMomentum` 추가: 과거 `lookback`봉 수익률의
   부호로 롱/플랫 결정 (Liu-Tsyvinski RFS/JF 논문의 절대모멘텀 방법론 그대로).
2. `momentum_candidate_grid()`: 논문이 실제로 검정한 지평(1일, 1/2/3/4주를
   1시간봉으로 환산 — 24/168/336/504/672봉)만 사용. `default_candidate_grid()`
   (H-0001 등록분)는 건드리지 않는다.
3. `scripts/run_swing_study.py --candidate-set momentum`으로 별도 하이포시스에서
   실행. H-0001과 후보군이 다르므로 새 hypothesis id(H-0002)로 등록한다.
4. **펀딩비 캐리 전략은 이번엔 코드화하지 않는다.** 과거 펀딩비 시계열이 없어서
   (ADR-0004) fail-closed로 이미 막혀 있고, 이 세션이 실제 데이터를 못 구한다.
   데이터가 생기면 별도 하이포시스로 등록한다.

## Consequences

- S등급 세 편 중 두 편(모멘텀)만 지금 전략 코드로 이어졌다; 캐리 논문은 데이터
  확보 전까지 설계만 기록.
- Management Science 논문은 초록만 확인했다는 한계를 남긴다 — 실제 캐리 전략을
  만들 때는 본문(포지션 구성, 비용 처리)을 다시 확인해야 한다.
- 앞으로 새 후보를 추가할 때도 이 등급 기준과 표를 갱신한다(새 ADR이 아니라 이
  ADR에 이어 쓰거나, 범위가 크게 바뀌면 새 ADR).
