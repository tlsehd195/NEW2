# ADR-0027: swing 등록 예산 4개 상향(SOLUSDT 지표투표 검증 한정)

**Status:** Accepted
**Date:** 2026-10-02
**Deciders:** 동동 (사람이 대화에서 "상향"으로 직접 선택), Claude Code session

## Context

`research/hypotheses.py`의 등록 예산은 "30일 안에 같은 패밀리(swing) 가설 3개"다. 2026-09-28~29에 swing
가설 H-0013, H-0015, H-0016이 등록되어 예산이 찼고, ADR-0026의 SOLUSDT 지표투표 가설(H-0022) 등록이
거부됐다. 선택지는 (a) 2026-10-28까지 대기(과적합 방지 기준 유지, 추천), (b) 예산을 4개로 상향이었고,
동동이 (b)를 선택했다.

## Decision

- 이번 한 번에 한해 `--max-per-window 4`로 H-0022를 등록한다. 검사 자체를 우회하지 않고 같은 `register_checked`
  경로의 `Budget` 인자만 사람이 승인한 값으로 넘긴다.
- 기본값(3)은 코드에서 바꾸지 않는다. `scripts/run_validation.py`는 3을 넘는 값에 `--budget-adr ADR-xxxx`
  (승인을 기록한 ADR)를 요구하고, `signal_validation.yml`에도 같은 입력(`max_per_window`, `budget_adr`)을 둔다.
  자동 루프가 예산을 임의로 키울 수 없다.
- 이번 예외는 H-0022에만 쓴다. 다음 swing 가설은 다시 예산 규칙(3개)을 따른다.

## Consequences

- 30일 안 swing 가설이 4개가 되어, 다중검정 부담이 늘었다. DSR은 등록된 전체 후보 수로 디플레이트되므로
  검증 기준 자체는 그대로다.
- H-0022 결과 라벨은 백테스트/INCONCLUSIVE 규칙(validation-status-guard)을 따른다.
- 새 후보 `swing_indicator_vote*`의 `markets`에 SOLUSDT를 추가했다(`configs/strategies.json`).
