# ADR-0049: 모의투자 대시보드를 bklit UI(React) 차트로

**Status:** Accepted
**Date:** 2026-10-09
**Deciders:** account owner, Claude Code session

## Context

ADR-0048 대시보드는 canvas로 직접 그렸다. 사용자가 bklit.com의 차트 부품(bklit UI, MIT)을 실제로 써 보라고 했다.
bklit UI는 shadcn 레지스트리로 배포되는 React + visx 부품이라 브라우저에서 바로 쓸 수 없고 빌드가 필요하다.
사용자 PC는 zip을 받아 파이썬으로만 돌린다.

## Decision

- 화면 소스는 `dashboard-ui/`(Vite + React + Tailwind). bklit 부품(캔들차트, 라인차트와 그 의존 부품)은
  `bklit.com/r/<이름>.json` 레지스트리에서 받아 `dashboard-ui/src/components/`에 그대로 두었다(shadcn 방식: 코드 소유).
  바꾼 곳은 시간축 표기(15분봉이라 날짜 대신 시:분)와 shimmering-text import 경로 두 군데뿐이다.
- 지표선(EMA·볼린저·돈치안), 진입·손절·현재가 선, 체결 삼각형, RSI 기준선은 bklit의 차트 문맥(`useChart`)에서
  축을 읽는 작은 SVG 층(`dashboard-ui/src/overlays.tsx`)으로 그린다.
- 빌드 결과(`scripts/dashboard_static/`)를 커밋한다. `run_dashboard.py`는 그 폴더의 파일과
  `/api/config`(종목 목록), `/api/snapshot`(기존과 같음)만 낸다. 사용자 PC에는 Node가 필요 없다.
- 데이터·매매 코드는 바꾸지 않는다. `dashboard.py`의 import 제한 테스트도 그대로다.

## Consequences

- 화면을 고치려면 `cd dashboard-ui && npm install && npm run build` 후 `scripts/dashboard_static/`를 함께 커밋해야 한다.
  CI는 Node 빌드를 돌리지 않으므로, 소스만 바꾸고 빌드를 빼먹으면 화면에 반영되지 않는다.
- 빌드 결과는 약 530KB(gzip 170KB)이고 외부 CDN 없이 로컬에서 열린다.
- ui.bklit.com과 ui.shadcn.com은 이 클라우드 환경에서 막혀 있어 shadcn CLI 대신 bklit.com/r을 직접 받았다.

## 후속: 실선 지표와 과거 스크롤 (2026-10-09)

- 지표선(EMA·볼린저·돈치안)을 점선 대신 실선으로 그린다(트레이딩뷰처럼). 볼린저 밴드 사이는 옅게 칠한다.
- 차트를 왼쪽으로 끌면 과거로 이동, 휠로 확대·축소, 더블클릭으로 처음 화면으로 돌아간다. 화면에 보이는 구간만 아래 패널(RSI·ROC·OBV)과 함께 잘라 그린다.
  이동 상태는 "N개 봉, 마지막 봉 시각 T"로 들고 있어서 5초 갱신에도 보던 자리가 안 움직인다.
- 서버는 `/api/snapshot?bars=N`(50~2000)을 받는다. 화면은 1000봉(약 10일)을 요청한다. 끝난 날의 15분봉은 한 번만 읽어
  메모리에 두고(`dashboard._DAY_CACHE`), 오늘·어제만 5초마다 다시 읽는다.
- 과거 봉은 저널(`var/data`)에 기록된 만큼만 있다. 모의투자를 켜기 전 구간을 거래소에서 받아 채우지는 않는다(읽기 전용 원칙 유지).
- 체결 표시를 매수/매도가 아니라 포지션에 한 일로 나눈다: 롱 진입(초록 ▲), 롱 청산(초록 속 빈 ◇), 숏 진입(빨강 ▼), 숏 청산(빨강 속 빈 ◇).
  서버가 체결을 같은 `client_order_id`의 `order_submit` 기록(`intent.purpose`: entry / stop / exit)과 이어서 `kind`를 준다
  (BUY=롱 진입 또는 숏 청산, SELL=숏 진입 또는 롱 청산). 주문 기록이 없는 체결은 `kind=None`이고 회색 ▲/▼로 남는다.
  예전에는 숏 진입도 "매도" 빨강이라 청산과 구분되지 않았다. 차트 위에 범례를 둔다.
- 청산된 거래 표에 두 가지 수익률을 보여 준다. 가격 수익률 = 진입가→청산가 변화를 거래 방향대로(수수료·레버리지 전),
  계좌 수익률 = 수수료·스프레드·슬리피지·펀딩을 뺀 순손익 ÷ 진입 때 계좌 잔고(`return_on_equity`). 열 제목에 기준을 적었다.
  값이 저장돼 있지 않은 옛 기록은 "-"로 둔다(만들어 내지 않음).
- 화면 설정 창(⚙ 설정): 지표(EMA·볼린저·돈치안·RSI·ROC·OBV)별 켜기/끄기·기간·색·굵기, 볼린저 폭·밴드 색칠, 체결 마커와 글자,
  진입·손절·현재가 선, 테마(자동/어둡게/밝게), 새로고침 간격, 처음 보일 봉 수, 불러올 과거 봉 수. 이 브라우저의 `localStorage`에 저장하고
  "기본값으로" 버튼이 있다. **화면 표시용일 뿐**이다: 전략이 쓰는 지표 기간과 매매 설정은 서버에 있고 건드리지 않는다
- 화면 전체 설정: 패널(현재가·포지션·계좌·지표별 판단·지표 현재값·최근 판단·청산된 거래·차트)마다 보이기/숨기기, 순서(▲▼), 너비(1/3·1/2·2/3·전체),
  청산 거래 표의 열 선택, 글자 크기, 차트 높이를 같은 설정 창에서 바꾼다. 마찬가지로 브라우저 저장·기본값 복원이며 표시 전용이다.
  ("지표 현재값"과 "지표별 판단" 카드는 전략 기준 값이라 설정과 무관하다).
- 기간을 바꿀 수 있게 하려고 차트의 지표선은 브라우저에서 계산한다(`src/indicators.ts`). `features/indicators.py`와 같은 식·같은 창
  (EMA·RSI는 기간×4)이라 기본 기간에서는 같은 값이다(`npm run check-indicators`로 서버 값과 비교, 최대 상대 차이 1e-15). 서버가 주는
  `overlays`는 이제 화면에서 쓰지 않지만 API에는 남겨 두었다. 앞쪽 봉은 기간×4만큼 선이 없다(웜업).
- Next.js 판(`dashboard-next/`): 사용자가 올린 별도 디자인(lightweight-charts)을 그대로 저장소에 넣었다. 같은 `/api/snapshot`을 `TRADER_API_URL`로 읽고,
  Vercel Analytics는 뺐다. 실행에 Node가 필요하므로 기본 화면은 계속 `dashboard-ui/`(파이썬만 필요)이고, 이 판은 선택 사항이다.
- 포지션 카드에 사용 증거금을 추가했다. 저장된 값이 아니라 `notional / exchange_leverage`(margin_policy.json, 미검증 가정)로 계산한 추정치이고, `run_dashboard.py`가 레버리지를 `read_snapshot`에 넘긴다.
- 계좌 잔고의 원화 표시: 서버가 `krw_live.json`(ADR-0041)의 환율로 `balance_krw`·`equity_krw`를 채운다. 파일이 없거나 읽을 수 없으면 비워 두고 화면은 "-"로 표시한다(fail-closed).
- 자산곡선·안전 상태 패널: `read_snapshot`이 `equity_curve`(outcome의 net_pnl 누적), `kill_switch`(킬 스위치 JSONL의 마지막 줄, 읽기만), `reconciliation`(safety 레이어의 마지막 재조정)을 돌려준다. 안전 파일은 수정하지 않고 로그 파일만 읽는다.
- 페이지 1 캘린더: `read_snapshot`의 `daily`(outcome을 한국시간 날짜로 묶음: 손익, 건수, 승수, 그날 첫 진입 때 계좌 잔고 대비 수익률)로 그린다. 기존 화면은 페이지 2로 옮겼다.
- 캘린더 날짜 검색은 화면 안에서만 동작한다(서버 호출 없음, `daily`에서 찾음). 페이지 1은 종목과 무관한 계좌 전체 기록이라 dashboard-next의 BTC/ETH 선택은 페이지 2에서만 보인다.
