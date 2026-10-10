# ADR-0057: 창 없이 켜는 시작·끄기 파일 (더블클릭)

**Status:** Accepted
**Date:** 2026-10-10
**Deciders:** account owner, Claude Code session

## Context

`start-dashboard-next.bat`을 켜면 검은 cmd 창이 3개 뜬다: 모의투자(`run_paper_trader.py`), 자료 서버(`run_dashboard.py`,
8765번 포트), 화면 서버(`pnpm start`, 3000번 포트). 옛 대시보드가 따로 뜨는 것은 아니다. 자료 서버가 옛 화면(`dashboard-ui`)도
같이 내보낼 뿐이라 <http://127.0.0.1:8765>로 열면 옛 화면이 보인다. 창이 3개라 닫으면 어느 것이 무엇인지 헷갈리고, 모의투자 창을
실수로 닫으면 매매가 멈춘다. 사용자는 프로그램처럼 더블클릭 한 번으로 켜기를 원한다.

## Decision

- `start-new2.vbs` 더블클릭: `scripts/launcher.py start`를 창 없이 실행한다. 모의투자·자료 서버·화면 서버를 뒤에서 띄우고(Windows
  `CREATE_NO_WINDOW`) 화면이 준비되면 브라우저를 연다. 각 출력은 `var/logs/*.log`에 남는다. 처음 한 번의 패키지 설치·빌드도 뒤에서
  하고 작은 안내창을 띄운다. Node.js가 없거나 설치·빌드가 실패하면 로그 위치를 알려 주는 안내창을 띄운다(콘솔이 없으므로).
- `stop-new2.vbs` 더블클릭: `var/paper/STOP` 파일을 만들어 모의투자가 상태를 저장하고 스스로 끝나게 한 뒤(최대 30초, 그래도 안 끝나면
  강제 종료) 나머지를 끈다. 창 없는 프로세스에는 Ctrl+C를 보낼 수 없고 강제 종료는 정상 종료 절차를 건너뛰기 때문이다.
  모의투자는 `src/cointrader/paper/stop_file.py`로 이 파일을 2초마다 확인하고, 시작할 때 낡은 STOP 파일은 지운다.
- `status-new2.bat`: 세 부분이 돌고 있는지와 모의투자 상태 파일의 마지막 저장 시각을 보여 준다. 대시보드에서도 "계좌 잔고"
  밑의 저장 시각과 헤더의 "실시간 연결"로 알 수 있다.
- 이미 돌고 있으면 중복 실행하지 않는다: 모의투자는 `trader.lock` 잠금, 자료 서버·화면은 포트가 열려 있는지로 판단하고, 다시 켜면
  브라우저만 연다.
- `start-dashboard-next.bat`은 창에서 오류를 직접 보고 싶을 때를 위해 그대로 둔다.

## Consequences

- Windows에서 직접 시험하지 못했다(개발 환경이 Linux). 같은 `launcher.py`의 시작·중복 실행 방지·종료는 Linux에서 확인했고
  Windows 전용 부분은 `CREATE_NO_WINDOW`, `taskkill`, 안내창뿐이다. 처음 쓸 때 문제가 있으면 `var/logs/launcher.log`를 본다.
- 창이 없어 모의투자가 돌고 있는지 눈에 안 보인다. `status-new2.bat`이나 대시보드로 확인한다. 컴퓨터가 꺼지거나 절전이면 모의투자도 멈춘다.
- 로그 파일이 계속 커진다(회전 없음). 필요하면 `var/logs/`를 지워도 된다(돌고 있는 동안 지우면 일부가 안 지워질 수 있다).
- 안전 파일, 라이브 승인, 자금 이동 경로는 건드리지 않는다.
