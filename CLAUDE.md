# CLAUDE.md

코인 스윙(→ 추후 스켈핑) 트레이딩 연구·검증·매매 시스템. 설계 배경은
`docs/decisions/ADR-0001-swing-first-bootstrap.md`.

## 개발

- `python3 -m pip install -e ".[dev]"` 후 `python3 -m pytest -q`.
- `src/`는 표준 라이브러리만 사용한다 (`tests/test_safety_boundaries.py`가 검사).
- 모든 datetime은 timezone-aware UTC. naive datetime은 버그로 취급한다.

## 절대 규칙

1. **검증 순서를 건너뛰지 않는다.** 사전등록(`validation/preregistration.py`) →
   locked window 확인 → 워크포워드 → PBO/DSR → held-out TEST 1회. TEST에 쓴 구간은
   즉시 `configs/locked_windows.json`에 추가하고 다시는 어떤 용도로도 쓰지 않는다.
   가설을 바꾸면 새 id로 다시 등록한다(기존 기록 수정 금지).
2. **킬 스위치 해제, 라이브 승인, APPROVED/DEPLOYED 전이, 실제 자금 이동
   (`funding/bridge.py`의 `execute_transfer`)은 사람만.** 이 경로를 호출하는 코드를
   `src/`나 자동 스크립트에 추가하지 않는다. AST 테스트가 막는다. 자금 이동은 매번
   새 승인이 필요하다(재사용 불가, `FundTransferApproval.quote_id`로 특정 견적에
   묶임).
3. **안전 파일**(`src/cointrader/live/kill_switch.py`, `safety_gate.py`, `approval.py`,
   `src/cointrader/funding/bridge.py`, `funding/approval.py`, `configs/live/`, `.env`)은
   사용자가 대화에서 해당 변경을 명시적으로 확인하기 전에는 수정하지 않는다
   (`.claude/hooks/protect-safety-files.sh`).
4. **fail-closed.** 입력이 없거나, 측정 불가하거나, 범위를 벗어나면 거래하지 않는다.
   문제를 조용히 넘기지 않고 이유를 기록한다.
5. **출처(provenance)를 숨기지 않는다.** 모든 캔들은 실제로 응답한 소스를 `source`에 남긴다.

## 운영 규칙

- ADR 번호는 손으로 고르지 않는다: `python3 scripts/adr_number.py new <slug> --title "..."`
  → 발급 즉시 커밋+푸시. 병합 직전 `python3 scripts/adr_number.py check`.
- 작업 단위를 마치면 브랜치를 방치하지 않는다: 테스트 통과 → PR → CI 통과 → main 병합까지
  그 세션 안에서 끝낸다.
- 여러 세션이 동시에 작업할 때 백그라운드 에이전트는 동시에 1개만 실행한다.
- 비밀값(API 키, 웹훅 URL)은 환경변수로만 받고 저장소에 커밋하지 않는다.
- 브랜치를 이어 쓰기 전에 `git merge-base --is-ancestor origin/main <branch>`로 최신 main을 포함하는지 확인한다.
  아니면 먼저 origin/main으로 맞춘다(stale 브랜치 재사용이 ADR 번호 충돌을 낸 NEW-의 사고 사례).
- 개발 중에는 관련 테스트만 돌리고, 전체 테스트 스위트는 병합 직전에 한 번 돌린다. 작은 관련 변경은 한 PR로 묶는다.
- llmwiki MCP(`.mcp.json`, `.wiki/`)는 문서의 2차 뷰다. 위키 내용을 사전등록/락 구간 같은 의사결정 경로에 쓰지 않는다.
