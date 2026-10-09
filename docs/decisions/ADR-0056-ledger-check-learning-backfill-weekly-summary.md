# ADR-0056: 보고서 부족분: 거래소 규칙 확인 도구·원장 일관성 검사·학습 보충 실행·주간 디스코드 요약

**Status:** Accepted
**Date:** 2026-10-10
**Deciders:** account owner (동동, "부족하다고 한거 진행"), Claude Code session

## Context

분석 보고서에서 남은 항목 T7, T9, T10, T14를 코드로 먼저 확인하고 처리했다. 가설 등록, 검증 실행, 안전 파일, 위험 한도, 멀티 심볼, 확률비례 사이징은 건드리지 않았다.

## Decision

**T7 거래소 규칙.** `configs/markets.json`의 `verified`는 계속 `false`다. 이 클라우드 환경과 GitHub Actions는 `fapi.binance.com`에 닿지 않아(이 세션에서 프록시 403 확인, Actions는 ADR-0012의 451) 실제 값을 확인하지 못했다. 현재 BTC(틱 0.1, 수량 0.001, 최소 주문 100)와 ETH(0.01, 0.001, 20) 값은 기억에서 옮긴 값이고 맞다고 단정하지 않는다. 대신 사용자 PC에서 돌릴 `scripts/verify_markets.py`를 만들었다(`exchangeInfo`를 받아 비교하고 다르면 표시, `--write`로 고치고 `verified=true`, 저장해 둔 JSON은 `--from-file`). 비교 로직은 `execution/market_check.py`(테스트 있음). `--write`는 규칙 4개와 `verified`만 바꾸고 `large_trade_quantity`는 건드리지 않는다.
영향: 위험 설정 버전 해시(`RiskConfig.version()`)와 `LiveTradingConfig.configuration_version()`은 `markets.json`을 포함하지 않아 해시는 변하지 않는다. 전략 검증에는 간접 영향이 있다. 백테스트·모의투자 체결의 수량 반올림과 최소 주문 거부가 이 값을 쓰므로, 값이 바뀌면 이전 선별·진단 수치와 직접 비교하지 말고 새로 돌려야 한다. 신호·가설·잠금 구간에는 영향 없다.

**T9 원장 일관성.** `validation/ledger_check.py`와 `scripts/check_ledgers.py`를 추가하고 CI에 단계로 넣었다(읽기 전용, ERROR면 실패, WARN은 출력만). 검사: 잠금 창 이름 중복·범위, `TEST-n:심볼` 자식이 부모와 같은 범위인지, 예약 창이 시장당 하나이고 잠금 창과 겹치지 않는지, 선별 장부의 학습/검증 범위가 잠금·예약 TEST를 읽지 않는지, 사전등록 id 중복, `data_end`로 끝나는 잠금 창이 없는 등록(WARN: TEST 대기 중이거나 잠금 누락), 후보 상태 기록이 등록된 가설을 가리키는지. 현재 장부는 오류 0, 경고 0.

**T10 학습 사이클.** 모의투자 프로세스에는 이미 연결돼 있었다(`scripts/run_paper_trader.py`가 `build_learning`을 `run()`에 넘기고, 이벤트마다 `maybe_run`). 빠진 것은 프로세스가 꺼져 있던 날의 보충이었다(ADR-0044는 수동 스크립트만). 이제 사이클이 한 번이라도 돈 적이 있으면 마지막 완료일 다음 날부터 최대 7일 전까지 놓친 날을 호출당 하루씩 오래된 순서로 채운다. 첫 실행은 전날만 한다. 관측 전용 유지: 새 코드는 `learning/cycle.py`뿐이고 저널 `learning` 층에만 쓴다. 안전 경계 테스트 통과.

**T14 주간 디스코드 요약.** `notifications/weekly_summary.py`, `scripts/send_weekly_summary.py`, `configs/weekly_summary.json`(`enabled: false`). 켜져 있어도 `DISCORD_WEBHOOK_URL` 환경변수가 없으면 보내지 않고, ISO 주당 한 번만 보낸다(`audit` 층에 기록). 내용은 최근 7일 모의투자 청산 거래 수·승률·손익비·비용 차감 후 손익·전략별 손익·분포 변화 일수이며, 모의투자는 실거래 성과가 아니라고 표시한다. `--dry-run`으로 글만 확인할 수 있다. 스케줄러(윈도우 작업 스케줄러 등)로 주 1회 호출해야 하며 프로세스에는 연결하지 않았다.

## Not done

T6(실제 스트림 확인)은 사용자 PC에서만 가능. T7의 실제 값 확인도 PC에서 `verify_markets.py` 실행 필요. 가설 등록, 검증 실행, 안전 파일, 위험 한도, T15, T16은 하지 않았다.

## Addendum (2026-10-10): 대시보드 시작 때 자동 확인

동동 요청("T7을 대시보드 켤 때 자동으로"). `start-dashboard-next.bat`가 모의투자·서버를 띄우기 전에 `verify_markets.py --write`를 실행한다. 바이낸스에 닿으면 규칙 4개를 거래소 값으로 맞추고 `verified=true`로 쓴다(이미 맞고 verified면 파일을 건드리지 않음). 닿지 않으면 메시지만 내고 파일을 그대로 두며(종료코드 2) 시작은 막지 않는다. 값이 바뀐 경우 화면에 DIFF가 찍히므로, 그날 이후 모의투자 수치는 이전과 직접 비교하지 말 것. 이미 떠 있는 모의투자는 재시작해야 새 값을 읽는다.
