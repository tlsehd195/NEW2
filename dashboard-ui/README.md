# dashboard-ui

모의투자 대시보드 화면 소스(ADR-0049). 차트는 [bklit UI](https://bklit.com)(MIT, `src/components/LICENSE-bklit-ui.txt`).

```bash
npm install
npm run dev      # http://localhost:5173, /api는 실행 중인 run_dashboard.py(8765)로 넘김
npm run build    # ../scripts/dashboard_static/ 에 빌드. 이 결과를 함께 커밋한다.
```

사용자 PC에서는 Node 없이 `python3 scripts/run_dashboard.py`만 실행한다.
