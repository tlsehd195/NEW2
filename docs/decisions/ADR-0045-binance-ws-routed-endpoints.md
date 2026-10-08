# ADR-0045: Binance 선물 웹소켓: /public·/market 경로 분리

**Status:** Proposed
**Date:** 2026-10-08
**Deciders:** account owner, Claude Code session

## Context

2026-10-08 사용자 PC(한국 윈도우)에서 모의투자를 1시간 40분 돌렸는데 `check_ws_quality.py`가 `decisions: 0`이었다.
저널을 보니 호가 통계(`book_stats_1m`) 202줄은 쌓였지만 15분봉 스트림 캔들, 체결 통계, 품질 이벤트는 0건이었다.
바이낸스는 2026-03에 USDⓈ-M 선물 웹소켓을 경로로 나눴다(공식 공지 "Important WebSocket Change Notice", 구 주소 폐기 예정일
2026-04-23). 경로 없는 주소(`wss://fstream.binance.com/stream`)는 `/public` 계층(bookTicker, depth)만 보내고,
`/market` 계층(aggTrade, markPrice, kline)은 **오류 없이 아무것도 안 보낸다.** 그래서 프로그램은 살아 있고(호가는 계속 옴)
15분봉만 영영 안 왔다. 이 샌드박스는 바이낸스에 닿지 못해 공식 문서를 웹 검색 요약으로만 확인했고, 위 사용자 관찰이 같은 결론을 가리킨다.

## Decision

- 스트림 이름마다 경로를 정한다(`binance_ws.stream_path`): bookTicker·depth → `public`, aggTrade·markPrice·kline → `market`.
  모르는 이름은 추측하지 않고 오류를 낸다.
- 모의투자는 경로마다 결합 스트림 연결을 하나씩, 모두 2개 연다(`routed_stream_urls`, `runner.live_stream_urls`).
- `MultiMessageSource`가 연결들을 하나의 메시지 흐름으로 합친다. 한 연결이라도 끊기면 오류로 올려 전부 다시 연결하고,
  어느 연결에서도 `read_timeout`(30초) 동안 메시지가 없으면 TimeoutError.
- 늦게 끝난 읽기 스레드가 새 연결의 메시지를 가로채지 않도록 `WebSocketMessageSource.messages`는 시작할 때 연결을 한 번만 잡는다.

## Consequences

- 이 변경은 **실제 바이낸스 접속으로 확인되지 않았다(UNVERIFIED).** 경로 이름과 스트림 분류는 공식 문서 요약과 사용자 저널
  관찰이 일치하는 근거이고, 시험은 고정 응답뿐이다. 사용자 PC 재실행에서 `check_ws_quality.py`의 `decisions`가 0보다 커야 확인된다.
- 이전에 쌓인 모의 기록은 15분봉이 한 번도 안 들어온 상태라 판단 근거로 쓰지 않는다.
- 연결마다 30초 읽기 제한이 따로 있어서, 한 연결이 조용해지면(aggTrade·markPrice는 항상 빠르게 온다) 합친 흐름이 아니어도 오류로 올라가 전부 재연결된다. 다만 연결 안에서 특정 스트림만 조용히 빠지는 경우(이번 사고의 모양)는 못 잡으므로 `check_ws_quality.py`의 `decisions` 점검이 계속 필요하다.
- 새 틀의 첫 실행 점검 기준은 `docs/runbooks/first-paper-run-ws-check.md`.
