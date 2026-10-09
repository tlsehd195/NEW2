# dashboard-ui

모의투자 대시보드 화면 소스(ADR-0049). 차트는 [bklit UI](https://bklit.com)(MIT, `src/components/LICENSE-bklit-ui.txt`).

```bash
npm install
npm run dev      # http://localhost:5173, /api는 실행 중인 run_dashboard.py(8765)로 넘김
npm run build    # ../scripts/dashboard_static/ 에 빌드. 이 결과를 함께 커밋한다.
```

사용자 PC에서는 Node 없이 `python3 scripts/run_dashboard.py`만 실행한다.

`npm run check-indicators`는 (실행 중인 `run_dashboard.py`에 대해) 브라우저에서 계산하는 지표(`src/indicators.ts`)가
서버/전략의 계산(`features/indicators.py`)과 같은 값을 내는지 확인한다. `src/indicators.ts`를 고쳤을 때 돌린다.
