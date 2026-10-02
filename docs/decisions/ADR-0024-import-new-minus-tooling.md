# ADR-0024: Import dev tooling skills and llmwiki MCP from NEW-

**Status:** Accepted
**Date:** 2026-10-02
**Deciders:** account owner (동동, 'archify 빼고 전부 진행'), Claude Code session

## Context

NEW- (주식 프로그램)에 쌓아 둔 Claude Code 개발 도구 중 NEW2에 쓸 만한 것을 골라
가져온다(2026-10-02). 훅 4종, `adr_number.py`, 스킬 2종(`backtest-integrity-review`,
`validation-status-guard`)은 이미 NEW2 main에 있었다. 나머지를 동동이 목록을 보고
archify만 빼고 전부 가져오라고 정했다.

## Decision

1. 스킬 15개를 `.claude/skills/`에 복사: tdd, diagnosing-bugs, codebase-design,
   research, grill-me, grilling, handoff, ponytail 계열 6개, task-observer,
   git-guardrails-claude-code. 모두 도메인 무관 범용 스킬이라 내용은 그대로 두었다.
   task-observer의 PNG 2개(3MB)만 뺐다. `skills-lock.json`은 출처/해시를 이 15개로 줄였다.
2. `skill-observations/`는 NEW-의 관찰 기록(주식 전용)을 빼고 빈 구조로 시작한다.
3. llmwiki MCP: `.mcp.json`과 `vendor/llmwiki-mcp/`(의존성 없는 번들, MIT)를 그대로 가져오고,
   `.wiki/`는 스키마(AGENTS.md)와 빈 인덱스만 둔다. NEW-의 위키 페이지는 주식 모듈 설명이라 제외.
   위키는 2차 뷰이며 의사결정 경로(사전등록, 락 구간)로 역류시키지 않는다.
4. 가져오지 않음: archify(8.5MB, 필요할 때만 쓰는 다이어그램 도구), 주식 데이터 수집용 워크플로,
   claude-mem/headroom/context7 설치 스크립트(NEW- ADR-0151에서 세션 시작 타임아웃 위험으로 폐기).

## Consequences

- 스킬은 선택 호출이라 기존 동작을 바꾸지 않는다. 훅과 안전 규칙은 변경 없음.
- `.mcp.json`이 llmwiki 서버를 등록하므로, 세션이 MCP 승인을 물을 수 있다.
- 쓰지 않는 스킬은 이후 삭제해도 된다(`skills-lock.json`도 같이).
