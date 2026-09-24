@echo off
rem NSFW Mask (Hardened) - run the full test suite.
rem ASCII-only for the same reason as start.bat.

setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo [ERROR] Virtual environment not found. Run start.bat instructions first.
  pause
  exit /b 1
)

echo ============================================================
echo   Running all four test files
echo ============================================================
echo.

set FAILED=0

for %%T in (test_security test_engines test_pipeline_stub test_api_e2e) do (
  echo ------------------------------------------------------------
  echo   %%T
  echo ------------------------------------------------------------
  venv\Scripts\python.exe "tests\%%T.py"
  if errorlevel 1 (
    echo   ^>^> %%T FAILED
    set FAILED=1
  ) else (
    echo   ^>^> %%T passed
  )
  echo.
)

echo ============================================================
if "%FAILED%"=="1" (
  echo   RESULT: some tests FAILED
) else (
  echo   RESULT: all tests passed
)
echo ============================================================
pause
