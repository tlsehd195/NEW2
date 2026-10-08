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
