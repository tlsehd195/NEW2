# NEW2 — 코인 15분봉·5분봉 단타(데이트레이딩)

암호화폐 트레이딩 연구·검증·매매 시스템입니다.
[NEW-](https://github.com/tlsehd195/NEW-) 프로젝트에서 검증된 설계 원칙(과최적화 검정,
잠긴 TEST 구간, 사람만 해제하는 킬 스위치, 사람 승인 없는 실거래 불가)을 이어받았고,
**15분봉 또는 5분봉 한 가지만 쓰는 롱·숏 단타**를 목표로 합니다(전략마다 시간단위 하나, 섞지 않음.
5분봉은 2026-10-10 허용, [ADR-0066](docs/decisions/ADR-0066-5m-timeframe-unlocked.md)). 한 번 거래에 약 3% 이상을 노리고
몇 시간 보유합니다(최대 12시간). 1분 스캘핑, 일봉 스윙, 여러 시간단위를 섞는 방식은
쓰지 않습니다. 결정 배경: [ADR-0030](docs/decisions/ADR-0030-daytrade-multitimeframe-proposal.md),
[ADR-0031](docs/decisions/ADR-0031-daytrade-kind-and-combined-cap.md),
[ADR-0032](docs/decisions/ADR-0032-daytrade-15m-vote.md). 예전 스윙 후보·가설·잠금은 기록으로만
남아 있습니다.

원화는 업비트(국내 신고 거래소)에 보관하고, 매매는 바이낸스 선물에서 합니다
([ADR-0002](docs/decisions/ADR-0002-binance-futures-leverage.md)). 둘 사이의 자금
이동은 사람이 매번 직접 승인해야만 실행됩니다
([ADR-0003](docs/decisions/ADR-0003-fund-bridge-and-tax-report.md)).

## 구성

| 경로 | 역할 |
| --- | --- |
| `src/cointrader/data/` | 캔들·오더북 모델, 업비트 REST, 스트림 우선 피드(재연결 + REST 공백 메우기), 레이트리밋, 품질 검사 |
| `src/cointrader/backtest/` | 오더북 깊이 체결 시뮬레이션, 캔들용 보수적 비용 모델, 미래참조 불가 백테스트(현물), 격리마진 레버리지·청산·펀딩비 백테스트(선물) |
| `src/cointrader/strategies/` | 15분봉 단타 지표 투표 후보 4종(`daytrade_indicator_vote_*`, 롱·숏, 12시간 시간 손절), 레지스트리 — 전부 CANDIDATE, 우위 주장 아님. 예전 스윙·스캘핑 후보는 기록용 |
| `src/cointrader/validation/` | 워크포워드, PBO/DSR(NEW-에서 복사), 잠긴 TEST 구간, 가설 사전등록, 검증 스터디 |
| `src/cointrader/risk/` | 변동성 역비례 사이징, 비중 상한, 레버리지 사이징·청산가 계산(바이낸스 공식 문서 근거, ADR-0004), fail-closed |
| `src/cointrader/live/` | 킬 스위치, 라이브 승인, 안전 게이트 (보호 파일) |
| `src/cointrader/evolution/` | 후보 상태 전이: 자동은 OOS_TESTED까지, 실거래는 사람 승인 |
| `src/cointrader/features/` | 추세·모멘텀·변동성·거래량·VWAP·돈치안·미시구조 피처(미래 데이터 없음), 레짐 분류 |
| `src/cointrader/execution/` | OrderIntent(결정적 주문 id), 멱등 실행 엔진, PaperBroker, Binance 클라이언트, 게이트 확인 LiveBroker, 재조정 |
| `src/cointrader/paper/` | 상시 페이퍼 트레이더(복구 절차·상태 저장·보호 손절) |
| `src/cointrader/research/` | 후보 수명주기 원장, 가설 중복/죽은 계열 검사, 연구 데이터셋 |
| `src/cointrader/journal/` | 계층형 추가 전용 저널(raw→normalized→feature→decision→execution→outcome), 보존·압축, 1분 집계 |
| `src/cointrader/notifications/` | 디스코드 웹훅, 등급별 알림(비밀값 가림) |
| `src/cointrader/funding/` | 업비트→바이낸스 KRW→USDT 자금이동, 사람 승인 필수 (보호 파일) |
| `src/cointrader/tax/` | FIFO 실현손익 계산 (세무 조언 아님) |

## 시작하기

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest -q
```

15분봉 단타 후보는 가설 사전등록 후 `scripts/run_validation.py`로 검증합니다. 등록 예산은
모든 종류 합산 30일 3건이며(ADR-0031), 등록은 사용자 승인이 필요합니다. 잠금 밖 구간에서
신호 빈도와 확률 보정을 미리 볼 수 있습니다(수익은 읽지 않음):

```bash
python3 scripts/diagnose_vote_frequency.py --help
python3 scripts/calibrate_vote.py --help
```

자금이동은 실제로 실행하기 전에 항상 사람이 터미널에서 직접 승인합니다(`funding/bridge.py`
는 `.claude/hooks/protect-safety-files.sh`로 보호되고, AST 테스트가 자동 코드 경로에서
호출되지 않는지 검사합니다):

```bash
python3 scripts/run_fund_transfer.py --krw-amount 1000000 \
  --destination binance_futures_wallet --max-krw-per-transfer 5000000
```

기본은 더미 클라이언트로 아무 자금도 움직이지 않는 `--dry-run`입니다. 실제 이체에는
자신의 API 키로 만든 `DomesticExchangeClient`/`WithdrawalClient` 구현이 필요하고,
업비트가 바이낸스 출금 주소를 트래블룰상 허용하는지 먼저 업비트에 직접 확인해야
합니다.

## 거래 시스템 (ADR-0015) — 기본 모드는 PAPER

```bash
python3 scripts/run_backtest.py --strategy daytrade_indicator_vote_h16_c0.6_v1 --symbol BTCUSDT \
  --start 2023-04-20 --end 2024-06-18            # 15분봉 탐색 백테스트 (BACKTEST 라벨)
python3 scripts/run_validation.py --help          # 사전등록 → 워크포워드 → PBO/DSR → TEST 1회 → 즉시 락
python3 scripts/run_paper_trader.py               # 상시 페이퍼 트레이딩 (실주문 불가)
python3 scripts/show_status.py                    # 상태 / 킬 스위치 / 재조정 / 저장 용량
python3 scripts/show_performance.py               # 페이퍼 성과 (전략×레짐×심볼×시간단위)
python3 scripts/reconcile_positions.py            # 로컬 vs 브로커 비교, 보고만 함
python3 scripts/run_maintenance.py                # 압축·보존 (보호 계층은 삭제 불가)
```

설정: `configs/paper.json`, `risk.json`, `markets.json`(거래 규칙 **미확인 자리표시**),
`retention.json`, `strategies.json`. Binance는 미국 리전에서 451을 돌려주므로 페이퍼
트레이더의 실시간 스트림은 허용 지역의 컴퓨터에서 돌려야 합니다.

## 아직 없는 것

- 라이브 연결: 사람 승인을 실행 중인 프로세스에 싣는 경로와 LiveBroker 생성(의도적으로
  사람 결정으로 남김, ADR-0015)
- 15분봉 단타 후보의 검증 결과 (전부 CANDIDATE, 가설 미등록. BTC 확률 보정은 기저율보다 못했음, ADR-0042)
- 학습 사이클 무인 스케줄러 (기록 → 학습 → 평가/승격 순환)
- 자금이동의 실제 거래소 연동(API 키, 업비트 트래블룰 화이트리스트 확인)
- 바이낸스 마진 등급표(MMR·Maintenance Amount)·실제 펀딩비 시계열 자동 수집 — 지금은
  둘 다 호출하는 쪽이 데이터를 직접 넘겨야 한다(fail-closed, ADR-0004)
- 교차 마진(cross margin), 한 계좌에서 여러 포지션 동시 운용 (ADR-0004는 격리마진·
  단일 포지션만 다룸)
