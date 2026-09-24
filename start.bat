@echo off
rem NSFW Mask (Hardened) - launcher
rem Kept ASCII-only on purpose: cmd.exe reads .bat using the OEM codepage,
rem so non-ASCII text would be garbled on machines using a different locale.

setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ---------------------------------------------------------------------
rem Settings. Everything here can be overridden from your environment:
rem set NSFW_PORT=9000  before running this script.
rem ---------------------------------------------------------------------
if not defined NSFW_HOST set NSFW_HOST=127.0.0.1
if not defined NSFW_PORT set NSFW_PORT=8000

rem The ViT classifier weights (~340 MB) are fetched from HuggingFace on the
rem first run. Direct access is usually blocked from mainland China, so this
rem mirror is preset. If the download fails, the app still runs in
rem detector-only mode - censoring keeps working.
if not defined HF_ENDPOINT set HF_ENDPOINT=https://hf-mirror.com

rem Set NSFW_NO_BROWSER=1 to stop this script from opening a browser.
if not defined NSFW_NO_BROWSER set NSFW_NO_BROWSER=0

if not exist "venv\Scripts\python.exe" (
  echo [ERROR] Virtual environment not found.
  echo         Create it with:
  echo             python -m venv venv
  echo             venv\Scripts\python.exe -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

rem ---------------------------------------------------------------------
rem Pick a free port starting from NSFW_PORT, scanning up to 20 ports.
rem "I don't know which port it ended up on" is worth solving once and for
rem all: if the requested port is taken we move up and say so explicitly.
rem ---------------------------------------------------------------------
set REQUESTED_PORT=%NSFW_PORT%
set CHOSEN_PORT=
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "$s=%REQUESTED_PORT%; for($p=$s; $p -lt ($s+20); $p++){ $l=$null; try { $l=[System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,$p); $l.Start(); $l.Stop(); Write-Output $p; break } catch { if($l){ try{$l.Stop()}catch{} } } }"`) do set CHOSEN_PORT=%%P

if "%CHOSEN_PORT%"=="" (
  echo [ERROR] No free port found in range %REQUESTED_PORT% to %REQUESTED_PORT%+19.
  echo         Set a different starting port:  set NSFW_PORT=9000
  echo.
  pause
  exit /b 1
)

if not "%CHOSEN_PORT%"=="%REQUESTED_PORT%" (
  echo.
  echo [NOTICE] Port %REQUESTED_PORT% is already in use.
  echo          Using port %CHOSEN_PORT% instead. ^(Set NSFW_PORT to change.^)
  echo.
)
set NSFW_PORT=%CHOSEN_PORT%
set APP_URL=http://127.0.0.1:%NSFW_PORT%

echo ============================================================
echo   NSFW Mask (Hardened)
echo ============================================================
echo.
rem Note: ">" must be escaped as "^>" inside echo, otherwise cmd treats it
rem as a redirection operator and dies with "> was unexpected at this time".
echo   ^>^>^>  OPEN THIS IN YOUR BROWSER:   %APP_URL%
echo.
echo   API docs : %APP_URL%/api/docs
echo   API key  : %~dp0data\api_key
echo.
if exist "data\api_key" (
  echo   Your API key is printed below. Paste it into the box at the
  echo   top right of the web page. Treat it as a password.
  echo.
  echo   ------------------------------------------------------------
  type "data\api_key"
  echo.
  echo   ------------------------------------------------------------
) else (
  echo   The API key does not exist yet. It will be generated
  echo   automatically on first start and saved to data\api_key.
  echo   Re-run this script afterwards to see it.
)

echo ============================================================
echo   Loading models can take a while on the first run.
echo   The browser opens automatically once the server is ready.
echo   Press Ctrl+C to stop.
echo ============================================================
echo.

rem ---------------------------------------------------------------------
rem Open the default browser as soon as the port accepts connections.
rem Runs in the background so the foreground uvicorn can start right away.
rem Polling the port (instead of a fixed sleep) avoids "can't reach this
rem page" when the models take longer than expected to load.
rem ---------------------------------------------------------------------
if "%NSFW_NO_BROWSER%"=="0" (
  start "" /min powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 600;$i++){try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',%NSFW_PORT%);$c.Close();Start-Process '%APP_URL%';break}catch{Start-Sleep -Milliseconds 500}}"
)

venv\Scripts\python.exe -m uvicorn backend.app:app --host %NSFW_HOST% --port %NSFW_PORT%

echo.
echo Server stopped.
pause
