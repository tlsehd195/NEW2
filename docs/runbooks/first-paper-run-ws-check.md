# 첫 모의투자에서 바이낸스 웹소켓 형식 확인하기

`data/binance_ws.py`의 파서는 바이낸스 공식 문서의 필드 이름(`E`, `T`, `p`, `q`, `m`, `U`, `u`, `pu`, `b`, `a`,
`k.x` 등)으로 만들었고, 개발 환경에서 바이낸스에 접속할 수 없어 **실제 메시지로는 한 번도 확인하지 못했다**
(ADR-0015, ADR-0040). 형식이 다르면 파서는 추측하지 않고 `malformed_message` 품질 이벤트를 내며 거래를 막는다.
따라서 첫 실행에서 아래 순서로 확인한다. 이 절차는 사용자 PC 또는 바이낸스에 접속되는 서버에서 한다.

## 1. 실행 (30분~1시간)

```
python3 scripts/run_paper_trader.py
```

`configs/paper.json`의 `record_raw`를 `true`로 바꾸면 체결·호가를 `raw` 층에 남겨 나중에 대조할 수 있다(용량이 커서
첫 확인 때만 권장). 15분봉 마감은 최소 한 번(15분 이상) 지나야 확인된다.

## 2. 점검

```
python3 scripts/check_ws_quality.py --hours 2
```

- `malformed: 0`이고 `decisions`가 0보다 크면 통과. `decisions`가 0이면 15분봉(kline) 메시지가 해석되지 않았을
  수 있으니 더 오래 돌려 본다.
- `malformed_samples`에 `KeyError`가 있으면 해당 이벤트의 필드 이름이 문서와 다른 것이다. 그 줄의 이벤트 이름
  (`aggTrade`, `bookTicker`, `kline`, `markPrice`)과 빠진 필드가 나온다.
- `unknown_event`는 처리하지 않는 스트림이라 거래를 막지 않는다(정상).

## 3. 눈으로 대조

실제 메시지 한 줄과 `binance_ws.py` 상단의 필드 목록, 그리고 공식 문서를 비교한다.
- 연결 주소: 연결 2개. `wss://fstream.binance.com/public/stream?streams=...`(호가: bookTicker·depth)와 `.../market/stream?streams=...`(aggTrade·markPrice·kline). 결합 스트림이라 응답이 `{"stream":..., "data":...}`로 감싸짐 (ADR-0045)
- 문서: https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams

## 4. 결과 기록

통과하면 ADR이나 `docs/PROJECT_STATUS.md`에 "실제 메시지 확인함(날짜, 실행 시간)"을 적는다. 실패하면 파서를 고치고
테스트에 **실제 메시지**를 고정 응답으로 추가한다(추측으로 고치지 않는다). 통과하기 전의 모의투자 결과는 신뢰하지 않는다.
