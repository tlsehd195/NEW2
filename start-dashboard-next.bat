@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo === NEW2 Next.js dashboard ===
echo.

rem 1) Python
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY ( where python >nul 2>nul && set PY=python )
if not defined PY (
  echo [ERROR] Python is not installed. Install it from https://www.python.org/downloads/ and check "Add python.exe to PATH".
  pause & exit /b 1
)

rem 2) Node.js
where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js is not installed. Install the LTS version from https://nodejs.org/ then run this file again.
  pause & exit /b 1
)

rem 3) pnpm (installed once)
where pnpm >nul 2>nul
if errorlevel 1 (
  echo Installing pnpm, one time only...
  call npm install -g pnpm
  if errorlevel 1 ( echo [ERROR] pnpm install failed. & pause & exit /b 1 )
)

rem 4) data server (reads var\, port 8765) in its own window
start "NEW2 data server" cmd /k %PY% scripts\run_dashboard.py

rem 5) build once, then start the page
cd dashboard-next
if not exist node_modules (
  echo Installing packages, one time only. This takes a few minutes...
  call pnpm install
  if errorlevel 1 ( echo [ERROR] pnpm install failed. & pause & exit /b 1 )
)
set TRADER_API_URL=http://127.0.0.1:8765
if not exist .next (
  echo Building the page, one time only...
  call pnpm build
  if errorlevel 1 ( echo [ERROR] build failed. & pause & exit /b 1 )
)

echo.
echo Open http://localhost:3000 in your browser. Close this window to stop.
start "" http://localhost:3000
call pnpm start
pause
