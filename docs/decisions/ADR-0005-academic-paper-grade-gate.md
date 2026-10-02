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

## 부록 (2026-10-02): 지표 앙상블(지표별 롱/숏 확신도 → 합산) 문헌 조사

동동님 아이디어: 유명 보조지표를 최대한 많이 넣고, 지표마다 "롱/숏 몇 % 확신"을
내게 한 뒤 전체를 합산해 진입 방향을 정한다. 요청: "관련 논문을 S/A/B로 등급 나눠
가져와 적용".

### 등급 기준 (위 기준을 이 주제에 맞게 구체화)

- **S**: 최상위 저널 + 크립토 직접 + 재현 가능한 방법. (기존 ADR-0005 S 기준 그대로)
- **A**: 최상위 학술 매체지만 크립토가 아니라 *방법론*을 옮겨 쓰는 경우, 또는
  크립토를 직접 다루지만 상위권 아닌 저널·프리프린트이면서 검정이 탄탄한 경우.
- **B**: 범위가 좁거나(특정 코인군) 대체로 부정적 결과, 또는 우리 설계에 직접
  연결되지 않는 참고용.

**핵심 발견: 이 아이디어(크립토에서 여러 지표 합산)를 직접 뒷받침하는 S등급 논문은
없다.** 크립토 기술적 지표 문헌은 혼재/부정적이다. 그래서 설계는 "수익이 난다"가
아니라 "과적합을 구조적으로 막는 형태"로 잡고, 검증은 기존 파이프라인에 맡긴다.

| 등급 | 논문 | 근거 → 설계 반영 |
| --- | --- | --- |
| S | Liu & Tsyvinski (2021) RFS; Liu-Tsyvinski-Wu (2022) JF (기존 채택분) | 크립토에서 검증된 신호는 1~4주 모멘텀. → 모멘텀류 지표(roc, ema_trend, macd, donchian)를 패널에 포함하되 가중을 임의로 올리지 않음 |
| A | Neely, Rapach, Tu & Zhou (2014) Management Science | 기술지표를 개별로 쓰거나 전부 회귀에 넣는 것보다 *예측 결합*이 안정적(주식). → 기본 합산은 **동일가중 로그오즈 평균**, 학습 가중은 옵션 |
| A | Sullivan, Timmermann & White (1999) J. Finance | 기술 규칙 다수를 시험하면 데이터 스누핑으로 유의성이 사라짐. → 지표 수·변형을 사전 고정, 후보 그리드 소수, 사전등록 필수 |
| A | Bailey, Borwein, López de Prado & Zhu, "Probability of Backtest Overfitting" | 시도 횟수 보정 없이는 최고 성과가 과적합. → 이미 `validation/pbo_dsr.py`로 구현됨; 이 후보도 그 문턱(PBO≤0.2, DSR≥0.95) 적용 |
| A | Gu, Kelly & Xiu (2020) RFS | 많은 예측변수를 규제 없이 쓰면 실패, 규제·단순 구조가 유리(주식). → 지표별 보정기는 L2 수축(정보 없으면 50%) |
| A | Niculescu-Mizil & Caruana (2005) ICML; Guo et al. (2017) ICML | "확신 %"는 보정(Platt/온도 스케일링) 없이는 확률이 아님. → 지표 점수→P(long)를 Platt 보정(2파라미터, 실현된 과거만 사용) |
| A | Hudson & Urquhart (2021) Annals of Operations Research, "Technical trading and cryptocurrencies" | 크립토(BTC/LTC/ETH/XRP)에서 다수 기술 규칙이 표본 내 유의, 다중검정 보정 후에도 상당수 유지. 단 2018 상반기 표본외에서 BTC는 수익 없음. → 기대치를 낮추고 표본외·고정 TEST 1회 유지 |
| B | Ahmed, Grobys & Sapkota (2020) Finance Research Letters (프라이버시 코인 10종) | 이동평균 규칙이 전체로는 매수후보유를 못 이김(Dash만 예외). → 부정적 증거로 기록, 코드 반영 없음 |
| B | Zhang et al., "Deep Learning for Digital Asset Limit Order Books" (arXiv 2010.01241) | 호가창 기반 딥러닝(크립토). 지표 합산이 아니라 스캘핑용 호가 데이터 필요 → 참고용 |

확인 한계: 검색 결과 제목·초록·요약 수준으로 확인했고 유료 본문(RFS/JF/Management
Science)은 열람하지 못했다. 위 "근거" 열은 해당 논문의 널리 알려진 결론이며, 정량
수치는 인용하지 않았다. 코드화에 쓰는 세부(보정 방식 등)는 본문 재확인이 필요하다.

### 코드 반영 (후보, 아직 미등록·미검증)

- `features/indicator_votes.py`: 12개 지표(추세 4, 모멘텀 4, 되돌림 2, 거래량 2)
  → 부호 있는 점수 → Platt 보정 P(long) → 동일가중 로그오즈 평균. 지표별 %를
  `Signal.features`(`p_<지표>`)에 그대로 남겨 "각 지표의 롱 확신 %"를 볼 수 있다.
- `strategies/indicator_vote.py`: 합산 확률 ≥ 0.60 & 지표 60% 이상 동의 시 진입,
  < 0.52에서 청산(히스테리시스; ADR-0006의 과회전 교훈), ATR 손절 필수.
- **성과 주장 없음.** 레지스트리 미등록, 새 hypothesis id로 사전등록 → locked
  window 확인 → 워크포워드 → PBO/DSR → TEST 1회를 거치기 전에는 검증된 전략이 아니다
  (validation-status-guard: 상태는 INCONCLUSIVE 이전의 *미검증 후보*).

### 지표 선정 원칙 추가 (2026-10-02, 동동님 요청)

"겹치는 지표 말고 서로 다른 역할로 넓게." 기본 패널(`DEFAULT_PANEL`)은 역할당 1개:
추세(ema_trend), 돌파/구조(donchian_pos), 모멘텀(roc), 과매수·과매도(rsi),
평균회귀(bollinger_b), 거래량 흐름(obv_slope). macd·ma_alignment·stoch·cci·vwap_dev·mfi는
위 지표와 사실상 중복이라 계산만 하고 기본 투표에서 뺐다. 변동성은 방향이 없어서
투표가 아니라 게이트로 둘 예정이고, 펀딩비·미결제약정은 캔들 밖 데이터라 미구현.
`redundancy()`로 측정: 합성 랜덤워크에서 평균 |상관| 전체 0.75 → 기본 패널 0.70.
추세·모멘텀 계열 4개가 아직 서로 상관이 높아서, 진짜로 다른 역할(변동성 게이트, 펀딩/OI)을
더하는 게 다음 단계다.
