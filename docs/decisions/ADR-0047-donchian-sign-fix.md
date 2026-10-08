# ADR-0047: donchian_pos 부호 수정

**Status:** Accepted
**Date:** 2026-10-09
**Deciders:** account owner, Claude Code session

## Context

ADR-0046 진단 중 발견: `indicator_votes.raw_scores`가 `lo, hi = don`으로 풀지만
`donchian()`은 (high, low)를 돌려준다. 그래서 `donchian_pos`는 범위 상단에서 음수, 하단에서
양수로 부호가 뒤집혀 있었다.

## Decision

`hi, lo = don`으로 고치고, 상승 추세에서 양수·하락 추세에서 음수인지 보는 테스트를 추가한다.

## Consequences

- Platt 보정이 기울기 부호를 자유롭게 학습하므로 투표 결과는 사실상 같다. 보정 전 점수의
  해석만 바로잡힌다.
- 이미 저장된 보정값이 있으면 부호가 반대로 맞춰져 있을 수 있다. 실행 중인 모의투자는 이
  변경을 받은 뒤 재시작해서 새 코드로 다시 맞춘다.
- 사전등록 구간·가설은 건드리지 않는다.
