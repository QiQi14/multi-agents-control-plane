@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "_ai_cli=%~dp0scripts\ai_cli.py"
set "_probe_file=%TEMP%\ai-python-probe-%RANDOM%-%RANDOM%.txt"

call :probe python ""
if "!_probe_ok!"=="1" goto run_selected
call :probe py "-3"
if "!_probe_ok!"=="1" goto run_selected
call :probe python3 ""
if "!_probe_ok!"=="1" goto run_selected

>&2 echo ERROR: No usable Python 3.10+ interpreter was found.
>&2 echo Probed candidates: python, py -3, python3. Each candidate was resolved through PATH/PATHEXT and executed with real Python code.
>&2 echo Next steps:
>&2 echo   1. In Windows Settings, disable the python.exe/python3.exe App Execution Aliases if they point to the Microsoft Store stub.
>&2 echo   2. Install Python 3.10 or newer and reopen this terminal.
>&2 echo   3. With a known interpreter, run: python scripts\ai_cli.py %*
del /q "!_probe_file!" >nul 2>nul
endlocal
exit /b 1

:probe
set "_probe_ok=0"
set "_probe_name=%~1"
set "_probe_selector=%~2"
set "_probe_display=!_probe_name! !_probe_selector!"
set "_resolved="
for /f "delims=" %%P in ('where "!_probe_name!" 2^>nul') do if not defined _resolved set "_resolved=%%P"
if not defined _resolved (
  >&2 echo [REJECT] !_probe_display!: not found via PATH/PATHEXT.
  exit /b 1
)

set "_probe_output="
call "!_resolved!" !_probe_selector! -c "import sys; print(f'AI_PYTHON_OK:{sys.version_info[0]}.{sys.version_info[1]}'); raise SystemExit(0 if __import__('operator').ge(sys.version_info[:2], (3,10)) else 3)" >"!_probe_file!" 2>&1
set "_probe_status=!errorlevel!"
set /p "_probe_output="<"!_probe_file!"
del /q "!_probe_file!" >nul 2>nul

if "!_probe_status!"=="0" (
  if "!_probe_output:AI_PYTHON_OK:=!"=="!_probe_output!" (
    >&2 echo [REJECT] !_probe_display!: !_resolved! returned unexpected probe output instead of the Python marker.
    exit /b 1
  )
  set "_selected_path=!_resolved!"
  set "_selected_selector=!_probe_selector!"
  set "_probe_ok=1"
  exit /b 0
)
if not "!_probe_output:Microsoft Store=!"=="!_probe_output!" (
  >&2 echo [REJECT] !_probe_display!: !_resolved! behaved like the Microsoft Store App Execution Alias stub.
  exit /b 1
)
if not "!_probe_output:App Execution Alias=!"=="!_probe_output!" (
  >&2 echo [REJECT] !_probe_display!: !_resolved! behaved like the Microsoft Store App Execution Alias stub.
  exit /b 1
)
if "!_probe_status!"=="3" (
  >&2 echo [REJECT] !_probe_display!: !_resolved! is below the supported Python 3.10 floor.
  exit /b 1
)
>&2 echo [REJECT] !_probe_display!: !_resolved! failed the real-code probe with exit !_probe_status!.
exit /b 1

:run_selected
call "!_selected_path!" !_selected_selector! "!_ai_cli!" %*
set "_dispatch_status=!errorlevel!"
endlocal & exit /b %_dispatch_status%
