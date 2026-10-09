# NEW2 대시보드 (Next.js 판)

15분봉 페이퍼 매매의 조회 전용 대시보드. 주문·설정 변경 기능은 없다. 파이썬 대시보드 서버(`scripts/run_dashboard.py`)의
`/api/config`, `/api/snapshot`을 읽는다. `dashboard-ui/`(파이썬만으로 실행되는 판)와는 별개다.

## Windows에서 더블클릭으로 켜기

1. 파이썬(<https://www.python.org/downloads/>, 설치 때 "Add python.exe to PATH" 체크)과 Node.js LTS(<https://nodejs.org/>)를 설치한다(한 번만).
2. 저장소 맨 위의 `start-dashboard-next.bat`을 더블클릭한다. 처음에는 패키지 설치와 빌드로 몇 분 걸린다.
3. 검은 창이 세 개 뜬다: 모의투자("NEW2 paper trading"), 자료 서버, 화면. 브라우저에서 <http://localhost:3000>이 열린다.
4. 이미 모의투자가 돌고 있으면(같은 폴더) 새 모의투자 창은 "already running"이라고 말하고 멈춘다. 같은 `var/` 상태 파일을 두 프로세스가
   쓰면 안 되기 때문이다.
5. 끄려면 **모의투자 창에서 Ctrl+C**(상태가 저장되고 꺼진다). 그다음 나머지 창을 닫는다. 창을 그냥 닫아도 다시 켜면 저장된 상태에서 이어간다.
   컴퓨터가 꺼지거나 절전이면 모의투자도 멈춘다.

## 직접 실행

```
python3 scripts/run_dashboard.py                      # 데이터 서버 (기본 127.0.0.1:8765)
cd dashboard-next
pnpm install
TRADER_API_URL=http://127.0.0.1:8765 pnpm dev         # 개발
TRADER_API_URL=http://127.0.0.1:8765 pnpm build && pnpm start   # 실행
```

`TRADER_API_URL`이 없거나 서버에 닿지 않으면 가짜 데모 데이터를 보여주고 헤더에 "데모"라고 표시한다(실제 기록이 아니다).
Node 22 이상 필요. `src/`의 표준 라이브러리 규칙과 무관한 화면 전용 코드다.
