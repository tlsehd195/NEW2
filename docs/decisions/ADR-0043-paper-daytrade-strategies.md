# ADR-0043: 모의투자 전략을 15분봉 단타 후보로 변경

**Status:** Accepted
**Date:** 2026-10-08
**Deciders:** account owner (동동), Claude Code session

## Context

목표는 15분봉 하나만 쓰는 롱·숏 단타로 정해졌다(ADR-0030~0032). 그런데 `configs/paper.json`의 `strategies`는
예전 스윙(1시간봉)·스캘핑(1분봉) 후보 4개로 남아 있어, 사용자가 첫 모의투자를 돌리면 단타가 아닌 전략이 실행됐을 것이다.
동동님은 2026-10-08에 투표는 그대로 두고 첫 모의투자로 가기로 했다(B).

## Decision

`configs/paper.json`의 `strategies`를 `daytrade_indicator_vote_h16_c0.6_v1`, `daytrade_indicator_vote_h48_c0.6_v1`
두 개로 바꾼다. 펀딩비·미결제약정 투표가 붙은 `_side_` 후보 2개는 모의투자에서 그 데이터가 실시간으로 들어오는지
확인하지 못해 첫 실행에서는 뺀다. 심볼(BTCUSDT, ETHUSDT), 시작 자금, 리스크 값(A안: 거래당 0.5%, 상한 2배, ADR-0037/0038)은
그대로다.

## Consequences

- 모의 트레이더가 15분봉 스트림만 구독한다(1분봉·1시간봉 구독이 없어진다).
- 후보는 여전히 CANDIDATE이고 가설 등록·예산·TEST와 무관하다. 모의 결과는 검증 결과가 아니다.
- 실제 바이낸스 메시지로는 아직 확인 전이라, 첫 실행은 `docs/runbooks/first-paper-run-ws-check.md` 절차로 형식부터 확인한다.
