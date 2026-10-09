# ADR-0051: 선별(워크포워드)과 결선(TEST 1회) 분리 (제안)

**Status:** Accepted (2026-10-09 동동님 승인, 구현됨)
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

## Decision

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

## 결정된 세부 사항 (2026-10-09)

- 자동 연구 루프(`AUTOMATED_ACTORS`)도 선별은 돌릴 수 있다. 결선 등록은 사람만 한다(동동님 "1").
- 결선 후보는 한 번에 3개 이하(`MAX_FINALISTS = 3`, 동동님 "3개로 구현").
- 예약은 시장 단위다(봉 크기와 무관하게 같은 시간대를 막는다). 결선은 선별과 같은 `--start/--end`로
  해야 TEST가 예약 구간과 정확히 같아진다. 결선이 끝나면 예약은 지워지고 잠긴 구간이 된다.
- 결선 DSR 시도 수는 등록 후보 수 + 선별 장부의 후보 수다. 결선 후보는 선별에서 한 번, 등록에서 한 번
  세어져 약간 보수적이다.
- 적용 범위: `scripts/run_validation.py`(swing/scalp/daytrade). 지금 쓰지 않는 xsec·grid 스크립트는
  예전 방식(등록 = TEST) 그대로이고 30일 예산이 계속 막는다.

## 구현

- `src/cointrader/validation/screening.py`: `run_screening`, `ScreeningLedger`, 예약 구간 읽기·쓰기·해제,
  `check_finalists`(사람, 3개 이하, 선별 장부에 있음, TEST = 예약 구간).
- `signal_study.py`: 워크포워드 부분을 `walk_forward_folds`/`deflated_sharpes`로 빼서 선별과 공유,
  `run_signal_study(screened_trials=...)`.
- `scripts/run_screening.py`(새 파일, TEST 봉은 불러오지도 않음), `scripts/run_validation.py`(결선 검사,
  `--strategies` 필수, 잠근 뒤 예약 해제), `trial_ledger.prior_trials(screened=...)`.
- CLAUDE.md 절대 규칙 1을 새 순서로 고쳤다. 30일 3개 예산 숫자는 그대로다.

## Consequences

- 좋은 점: TEST를 보지 않는 실험은 한도 없이 할 수 있어 개선 속도가 빨라진다. TEST를 보는 횟수는
  지금처럼 묶인다.
- 대가: 선별을 많이 할수록 DSR 시도 수가 늘어 통과가 어려워진다(의도한 것). 선별에서 고른 후보는
  워크포워드 구간에 맞춰진 상태라, 결선 TEST가 유일한 독립 확인이다. 모의투자 기록은 그 다음의
  전진 검증으로 남는다.
- 이 변경으로 가설을 등록하거나 예산을 쓰거나 잠긴 구간을 바꾸지 않았다. 아직 선별을 돌리지 않아
  `research/screening.jsonl`과 `configs/reserved_windows.json`은 첫 선별 때 생긴다.
