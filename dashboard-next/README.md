# NEW2 대시보드 (Next.js 판)

15분봉 페이퍼 매매의 조회 전용 대시보드. 주문·설정 변경 기능은 없다. 파이썬 대시보드 서버(`scripts/run_dashboard.py`)의
`/api/config`, `/api/snapshot`을 읽는다. `dashboard-ui/`(파이썬만으로 실행되는 판)와는 별개다.

## Windows에서 켜기

1. 파이썬(<https://www.python.org/downloads/>, 설치 때 "Add python.exe to PATH" 체크)과 Node.js LTS(<https://nodejs.org/>)를 설치한다(한 번만).
2. 저장소 맨 위의 `start-new2.vbs`를 더블클릭한다. 검은 창 없이 모의투자·자료 서버·화면 서버가 뒤에서 켜지고, 주소창 없는
   독립 창(Edge, 없으면 Chrome)이 열린다. 처음 한 번은 설치와 빌드로 몇 분 걸리고 안내창이 뜬다.
3. **창을 닫으면 전부 같이 꺼진다.** 모의투자는 상태를 저장하고 꺼지며, 창이 닫혀 있는 동안은 모의투자도 멈춘다(포지션 손절 판단도 멈춘다).
   다시 켜면 저장된 상태에서 이어간다. 창이 비정상으로 꺼지거나 PC가 꺼졌어도 다음에 켤 때 남은 것을 먼저 정리한다.
4. 이미 켜져 있을 때 한 번 더 더블클릭하면 창만 하나 더 열린다.
5. 기록은 `var/logs/`에 있다. 상태 확인은 `python scripts/launcher.py status`. 바탕화면 바로가기: `start-new2.vbs` 우클릭 →
   보내기 → 바탕 화면(바로 가기 만들기).
6. 화면을 새로 빌드해야 할 때(코드를 받은 뒤)는 `dashboard-next/.next` 폴더를 지우고 켠다.

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

