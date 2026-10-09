# ADR-0051: 선별(워크포워드)과 결선(TEST 1회) 분리 (제안)

**Status:** Proposed (문서만: 코드·규칙·예산 변경 없음, 가설 등록 없음)
**Date:** 2026-10-09
**Deciders:** account owner (동동, "A" 선택 2026-10-09), Claude Code session

## Context

- 지금 NEW2에서 가설 하나를 등록하면 `scripts/run_validation.py`가 한 번에 끝까지 간다:
  사전등록 → 워크포워드 → PBO/DSR → TEST 1회 → TEST 구간 잠금(`signal_study.py` 1~20행,
  `run_validation.py` 115~118행). 워크포워드만 돌려 보는 공식 경로가 없다.
- 그래서 "후보를 몇 개 시도했나"를 등록 횟수로만 막는다: 30일에 종류별 3개, 전체 3개
  (`research/hypotheses.py`의 `Budget`, ADR-0031). 3개를 다 써서 2026-10-29 11:32 KST까지
  새 전략을 올릴 수 없다.
- 참고 저장소 tlsehd195/NEW-(주식)에는 등록 횟수 제한이 없었다. 대신 ADR-0222(NEW-)에서
  **선별과 결선을 나눴다**: 워크포워드 구간(TRAIN+VALIDATION)만 쓰는 선별은 몇 번이든 하고,
  좁혀진 결선 후보만 held-out TEST를 1번 보고 그 구간을 잠근다(`--skip-held-out`,
  `final_exam` 입력). 시도가 많아진 대가는 누적 시도 장부로 DSR 기준을 올려서 치른다
  (NEW- ADR-0232, NEW2에는 `validation/trial_ledger.py`로 이미 이식됨).
- NEW2에서도 등록 없이 하는 측정(ADR-0042 보정, ADR-0046 겹침 진단)은 이미 있지만, 수익률을
  보는 선별은 규칙상 자리가 없다.

## Decision (제안)

1. **선별 실행(screening)을 새로 둔다.** 같은 워크포워드·PBO·DSR을 TRAIN+VALIDATION에서만
   돌리고 TEST는 읽지 않는다. 선별 결과로는 어떤 후보도 BACKTESTED/OOS_TESTED로 올라가지 않는다.
2. **선별 결과는 지우지 못하는 장부에 남긴다** (`research/screening.jsonl`, 후보 id·시장·
   봉·구간·폴드 수익률·시각). 이 장부의 후보 수는 결선 DSR의 시도 횟수(`signal_study.py`의 `extra_trials`, 지금은 등록된 후보 수)에 더해진다.
   많이 선별할수록 결선 통과 기준이 자동으로 엄격해진다.
3. **결선용 TEST 구간을 미리 예약한다.** 선별을 처음 할 때 그 범위의 마지막 20%(지금
   `build_chronological_split`과 같은 비율)를 `configs/reserved_windows.json`에 기록하고,
   이후 그 시장의 선별은 예약 구간을 건드리면 거부된다. 결선은 이 예약 구간을 TEST로 쓰고,
   끝나면 지금처럼 `locked_windows.json`으로 옮겨 잠근다.
4. **30일 예산은 결선(등록)에만 센다.** 숫자 3은 그대로 둔다. 선별 횟수에는 상한을 두지 않고,
   2번의 장부와 DSR 보정으로 대신한다.
5. **결선 후보는 선별 장부에 있는 것만**, 한 번에 3개 이하로 등록할 수 있다.
6. CLAUDE.md 절대 규칙 1의 순서를 "선별(선택) → 사전등록 → 결선 TEST 1회 → 잠금"으로 고친다.

## 아직 정하지 않은 것 (승인 때 함께 결정)

- 자동 연구 루프(`AUTOMATED_ACTORS`)도 선별을 돌릴 수 있게 할지. 제안: 허용, 결선 등록은 지금처럼 사람만.
- 결선 후보 상한 3개가 맞는지.

## Consequences

- 좋은 점: TEST를 보지 않는 실험은 한도 없이 할 수 있어 개선 속도가 빨라진다. TEST를 보는 횟수는
  지금처럼 묶인다.
- 대가: 선별을 많이 할수록 DSR 시도 수가 늘어 통과가 어려워진다(의도한 것). 선별에서 고른 후보는
  워크포워드 구간에 맞춰진 상태라, 결선 TEST가 유일한 독립 확인이다. 모의투자 기록은 그 다음의
  전진 검증으로 남는다.
- 구현 범위(승인 뒤): `run_signal_study`에서 TEST 단계를 뺀 선별 함수, `scripts/run_screening.py`,
  예약 구간 검사, `extra_trials`·`trial_ledger`가 선별 장부도 세도록 수정, `check_new_hypothesis`에 5번 검사, 테스트.
- 이 ADR은 제안만 한다. 규칙·예산·코드·잠긴 구간은 바뀌지 않았고 가설도 등록하지 않았다.
