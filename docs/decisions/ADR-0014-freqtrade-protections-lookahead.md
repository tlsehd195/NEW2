# ADR-0014: Freqtrade-style protections and look-ahead checks

**Status:** Accepted
**Date:** 2026-09-28
**Deciders:** account owner, Claude Code session

## Context

사용자가 "frequant"라는 서비스가 NEW2와 비슷해 보인다며 참고할 만한 것을 가져오라고
했다. 그 이름으로 찾은 것은 옛 Quantopian(Zipline) 백테스트 엔진을 감싼 오픈소스,
영국 퀀트 채용회사, 개인 블로그뿐이었고 국내 코인 서비스는 확인하지 못했다. 발음이
가장 가까운 코인 도구는 오픈소스 봇 **Freqtrade**이고, 그중 NEW2에 없던 두 가지가
어느 쪽이든 쓸모 있어 먼저 가져왔다. Freqtrade의 hyperopt·커뮤니티 전략은 과최적화
위험이 커서 가져오지 않는다(ADR-0005, PBO/DSR 기준 유지).

1. **Protections** (`StoplossGuard`, `MaxDrawdown`, `CooldownPeriod`): 연속 손절이나
   낙폭 뒤 일정 시간 신규 진입을 막는 규칙. NEW2에는 킬 스위치(사람만 해제)만 있고,
   그보다 가벼운 "잠깐 쉬기" 층이 없었다.
2. **lookahead-analysis / recursive-analysis**: 전략이 미래 데이터를 보거나, 가용
   과거 길이에 따라 신호가 달라지는지 행동으로 검사. `PrefixView`는 인덱싱만 막고,
   데이터 전체로 미리 계산한 지표나 view 우회는 못 막는다.

## Decision

- `src/cointrader/risk/protections.py`: 순수 함수 `evaluate_protections`. 보호 규칙은
  **신규 진입만** 시간 제한으로 막고 스스로 풀린다. 포지션 청산이나 킬 스위치에는
  손대지 않는다(킬 스위치 해제는 여전히 사람만). 입력에 NaN/무한대가 있으면 진입을
  막고 이유를 남긴다(fail-closed).
- `src/cointrader/validation/lookahead.py`: `check_lookahead`(전체 데이터 vs t에서
  잘린 데이터의 신호 비교, 데이터로 전략을 만드는 경우 `factory`로 검사)와
  `check_warmup_sensitivity`(전체 과거 vs 엔진이 주는 최소 과거 warmup+1봉 비교).
  전략이 예외를 던지면 통과가 아니라 발견으로 기록한다.
- 등록된 모든 후보 그리드(baselines, momentum, adaptive ensemble)를 두 검사에 통과시키는
  테스트를 추가했다. 현재 전부 통과한다(합성 데이터 기준).

## Consequences

- 새 전략을 등록하기 전에 두 검사를 통과시키는 것이 테스트로 강제된다(기존 그리드).
  새 그리드를 추가하면 같은 테스트 파라미터에 넣는다.
- protections는 아직 백테스트 엔진·페이퍼 트레이딩에 연결되지 않았다. 상시 페이퍼
  트레이딩 프로세스를 만들 때 진입 직전 게이트로 붙인다.
- 사용자가 말한 "frequant"가 다른 서비스로 확인되면 그 서비스 기준으로 다시 본다.
