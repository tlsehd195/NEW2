# ADR-0023: 보정된 지표 투표 앙상블 (후보, 미검증)

**Status:** Proposed
**Date:** 2026-10-02
**Deciders:** account owner (동동, 아이디어·역할 분리 요청), Claude Code session

## Context

동동님 아이디어: 유명 지표 여러 개가 각자 "롱/숏 몇 % 확신"을 내고, 전체를 합산해
진입 방향을 정한다. 이후 "겹치는 지표 말고 서로 다른 역할로 넓게"를 요청했다.
논문 조사 결과와 등급표는 ADR-0005 부록(2026-10-02). 크립토에서 이 방식을 직접
뒷받침하는 S등급 논문은 없다. 기존 적응형 앙상블(H-0003/H-0004)은 과회전으로 비용에
무너진 전례가 있다(ADR-0006).

## Decision

1. 각 지표는 부호 있는 점수를 내고, **Platt 보정**(2파라미터, 실현된 과거만, L2 수축)으로
   P(long)이 된다. 정보가 없으면 50%. (Niculescu-Mizil & Caruana 2005; Guo 외 2017)
2. 합산은 **동일가중 로그오즈 평균**. 학습 가중은 옵션이고 기본이 아니다.
   (Neely 외 2014: 예측 결합; Sullivan 외 1999·Bailey 외 PBO: 자유 파라미터는 숨은 시도)
3. 기본 패널은 **역할당 지표 1개** 6개: ema_trend, donchian_pos, roc, rsi, bollinger_b,
   obv_slope. 중복 지표는 계산만 하고 투표에서 제외. `redundancy()`로 중복도 측정.
4. 진입은 합산 P ≥ 0.60이고 지표 60% 이상이 같은 방향일 때, 청산은 P < 0.52
   (히스테리시스, ADR-0006의 과회전 교훈). ATR 손절 필수. 입력이 빠지면 거래 안 함(fail-closed).
5. 변동성은 방향이 없어 투표가 아닌 **게이트**로, 펀딩비·미결제약정은 데이터 확보 후 별도 ADR로.

## Consequences

- 지표별 확신 %가 `Signal.features`(`p_<지표>`)에 남아 사후 분석이 된다.
- 추세·모멘텀 4개가 아직 상관이 높다(합성 평균 |상관| 0.70). 진짜 다른 역할 추가가 다음 단계.
- 성과 주장 없음. 레지스트리 미등록. 새 hypothesis id 사전등록 → locked window →
  워크포워드 → PBO/DSR → TEST 1회 전에는 미검증 후보다.
- NEW-에서 승인받아 가져온 진단 도구(`validation/reality_check_spa.py`, `trial_ledger.py`,
  `signal_ic.py`)는 이 후보를 검증할 때 쓴다. 아직 파이프라인에 연결하지 않았다.
