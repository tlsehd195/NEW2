# ADR-0001: 스윙 우선 착수, 업비트 KRW 현물, NEW- 재사용 범위

**Status:** Accepted
**Date:** 2026-09-28
**Deciders:** account owner, Claude Code session

## Context

동동님의 NEW- 저장소(미국 주식, 일봉, 팩터 연구/백테스트/페이퍼트레이딩)에서 검증된
원칙과 일부 코드를 가져와 코인 스켈핑/스윙 프로그램을 새로 시작한다. NEW-에서는 51개
전략·팩터 후보가 전부 SPY 대비 우위 입증에 실패했고, 일부는 held-out TEST에서 크게
언더퍼폼했다. 스켈핑은 수수료·스프레드 구조상 그보다 더 불리하게 시작한다.

## Decision

1. **시간 단위: 스윙(1시간봉~일봉)부터.** 워크포워드 + PBO/DSR + locked windows로
   통계적 우위가 확인된 뒤에만 더 짧은 시간 단위로 내려간다.
2. **거래소 기본값: 업비트 KRW 현물.** 착수 지시문에 거래소 지정이 없어 선택함.
   이유: 원화 입출금이 되는 국내 거래소, 공개 시세 API는 키 없이 사용 가능, 현물이라
   레버리지·청산 위험이 없다. 거래소 접근은 `CandleHistory`/`CandleStream` 프로토콜
   뒤에 있어 바이낸스 등은 어댑터 추가로 붙일 수 있다(레이트리밋 방식은 거래소별로
   새로 구현: 업비트 `Remaining-Req`, 바이낸스 weight).
3. **선물 제외.** 사이징(`risk/sizing.py`)은 현물 전용이며 레버리지 파라미터가 없다.
   선물은 레버리지·증거금·청산가 설계를 별도 ADR로 먼저 한 뒤에만 추가한다.
4. **NEW-에서 그대로 복사**(commit 2251c2e):
   `validation/pbo_dsr.py`(+ 테스트), 디스코드 전송부(`truncate_for_discord`,
   `send_discord_message`), `scripts/adr_number.py`, `.claude/hooks/`의
   `block-dangerous-git.sh`, `protect-safety-files.sh`(보호 경로만 이 저장소에 맞게 변경),
   `check-adr-numbers-before-merge.sh`.
5. **구조만 참고해 새로 작성:** 데이터 파이프라인(스트림 우선 + 재연결 + REST 공백
   메우기, provenance 기록), 데이터 품질(이슈 명시 기록), 워크포워드(월 → 시간/분 단위),
   locked windows(JSON 레지스트리, 겹치면 예외), 사전등록(해시 고정, 누적 시행 수를
   DSR에 반영), 체결비용(오더북 깊이 시뮬레이션 + 캔들용 보수적 모델), 킬 스위치,
   라이브 승인 게이트, 후보 상태 전이(자동은 OOS_TESTED까지, APPROVED/DEPLOYED는
   사람만), 거래 기록.
6. **가져오지 않음:** NEW- 백테스트 엔진 본체, PIT 유니버스, 기업활동, DuckDB 스키마,
   하루 1회 cron 페이퍼 루프, Alpaca 연동.
7. `src/`는 표준 라이브러리만 사용한다(NEW-와 동일한 경계, 테스트로 검사).

## Consequences

- 첫 연구 대상은 KRW-BTC 1시간봉, 고정된 8개 기준 전략 그리드
  (`strategies/baselines.py`). 이 그리드는 결과를 보기 전에 고정했다.
- 스켈핑용 웹소켓 스트림, 페이퍼 트레이딩 상시 프로세스, 학습 사이클 스케줄러는
  아직 없다. 각각 별도 작업으로 추가한다.
- 캔들 기반 비용 모델의 기본값(스프레드 2bp, 임팩트 계수)은 가정이다. 실제 오더북을
  기록해 `simulate_market_fill`로 보정하기 전까지 백테스트 결과는 낙관적일 수 있다.
