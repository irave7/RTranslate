@echo off
setlocal DisableDelayedExpansion
set "RT_PY_HOME=%LOCALAPPDATA%\RTranslate\agent-bootstrap\cpython-3.13.16-x64"
set "runner=%RT_PY_HOME%\python.exe"
if exist "%runner%" goto bundled
if "%RTRANSLATE_MANAGED_BOOTSTRAP%"=="1" goto prepare
set "RT_SYSTEM="
for /f "delims=" %%P in ('where.exe python.exe 2^>nul ^| findstr.exe /i /v /l /c:"\Microsoft\WindowsApps"') do if not defined RT_SYSTEM set "RT_SYSTEM=%%P"
if defined RT_SYSTEM (
    "%RT_SYSTEM%" -I -c "import sys; sys.exit(sys.version_info[:2] < (3, 9))" >nul 2>nul
    if not errorlevel 1 goto system
)
set "RT_LAUNCHER="
for /f "delims=" %%P in ('where.exe py.exe 2^>nul ^| findstr.exe /i /v /l /c:"\Microsoft\WindowsApps"') do if not defined RT_LAUNCHER set "RT_LAUNCHER=%%P"
if defined RT_LAUNCHER (
    "%RT_LAUNCHER%" -3 -I -c "import sys; sys.exit(sys.version_info[:2] < (3, 9))" >nul 2>nul
    if not errorlevel 1 goto launcher
)
:prepare
rem Fetch a private, pinned Windows x64 Python directly from python.org.
set "RT_PY_WORK=%RT_PY_HOME%-pending-%RANDOM%-%RANDOM%"
mkdir "%RT_PY_WORK%" 2>nul || exit /b 1
set "RT_PY_ZIP=%RT_PY_WORK%\python.zip"
set "RT_PY_EXTRACT=%RT_PY_WORK%\runtime"
set "RT_PY_URL=https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip"
set "RT_PY_SHA=97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297"
set "ps=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
echo Preparing the private Windows x64 runtime from python.org... 1>&2
"%ps%" -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile($env:RT_PY_URL,$env:RT_PY_ZIP)" || goto failed
"%ps%" -NoProfile -NonInteractive -Command "if ((Get-FileHash -Algorithm SHA256 -LiteralPath $env:RT_PY_ZIP).Hash -ne $env:RT_PY_SHA) {exit 1}" || goto failed
"%ps%" -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; Expand-Archive -LiteralPath $env:RT_PY_ZIP -DestinationPath $env:RT_PY_EXTRACT; try {[IO.Directory]::Move($env:RT_PY_EXTRACT,$env:RT_PY_HOME)} catch [IO.IOException] {if (!(Test-Path -LiteralPath (Join-Path $env:RT_PY_HOME 'python.exe'))) {throw}}" || goto failed
rmdir /s /q "%RT_PY_WORK%"
goto bundled
:failed
rmdir /s /q "%RT_PY_WORK%"
echo Could not prepare the verified Python runtime. Retry with network access or use the verified RTranslate skill package. 1>&2
exit /b 1
:bundled
"%runner%" -I -B -X utf8 "%~dp0rtranslate.py" %*
exit /b %errorlevel%
:launcher
"%RT_LAUNCHER%" -3 -I -B -X utf8 "%~dp0rtranslate.py" %*
exit /b %errorlevel%
:system
"%RT_SYSTEM%" -I -B -X utf8 "%~dp0rtranslate.py" %*
exit /b %errorlevel%
