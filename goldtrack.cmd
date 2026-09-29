@echo off
rem goldtrack launcher — runs from anywhere, picks a working Python itself.
rem   goldtrack.cmd check
rem   goldtrack.cmd monitor
rem   goldtrack.cmd serve
setlocal
cd /d "%~dp0"

rem NOTE: do not write `if not defined X cmd && set Y` on one line — batch runs
rem the `&&` branch even when the `if` is false and there is no command to skip,
rem which silently overwrote the interpreter choice here. Keep them separate.

set "PY="

rem 1) the newest per-user CPython install (most reliable on Windows)
set "LASTPY="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
  if exist "%%D\python.exe" set "LASTPY=%%D\python.exe"
)
if defined LASTPY set "PY="%LASTPY%""

rem 2) otherwise the Windows py launcher, if it works
if not defined PY (
  py -3 --version >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)

rem 3) otherwise whatever `python` is, but only if it actually runs
if not defined PY (
  python --version >nul 2>&1
  if not errorlevel 1 set "PY=python"
)

if not defined PY (
  echo.
  echo   Could not find a working Python 3.10+ interpreter.
  echo   Install it from https://www.python.org/downloads/ and tick
  echo   "Add python.exe to PATH" during setup, then run this again.
  echo.
  exit /b 1
)

if "%~1"=="" (
  %PY% -m goldtrack --help
  echo.
  echo   Common commands:
  echo     goldtrack.cmd monitor     live terminal monitor
  echo     goldtrack.cmd serve       live web monitor  ^(http://127.0.0.1:8787^)
  echo     goldtrack.cmd brief       full one-shot report
  echo     goldtrack.cmd check       verify every feed is reachable
  exit /b 0
)

%PY% -m goldtrack %*
