@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul 2>nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
pushd "%~dp0" || goto :failed

echo ============================================
echo   Yulin Heritage - Local Website Launcher
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Project Python environment was not found.
  echo Run: python -m venv .venv
  echo Then: .venv\Scripts\python.exe -m pip install -r backend\requirements.txt
  goto :failed
)
where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js 22.13 or newer is required. Check your PATH.
  goto :failed
)
where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm was not found. Check your Node.js installation.
  goto :failed
)
if not exist "apps\web\node_modules\vinext" (
  echo Installing frontend dependencies...
  call npm --prefix "apps\web" ci
  if errorlevel 1 goto :failed
)

".venv\Scripts\python.exe" -X utf8 "scripts\dev.py" --open %*
set "YULIN_EXIT=%ERRORLEVEL%"
if not "%YULIN_EXIT%"=="0" goto :failed
if /i "%~1"=="--check" goto :done
echo.
echo Services stopped, or an existing website was opened.
goto :finish

:failed
set "YULIN_EXIT=1"
echo.
echo [ERROR] Startup did not complete. See the error above and README.md.

:finish
if /i "%~1"=="--check" goto :done
if not "%YULIN_NO_PAUSE%"=="1" pause

:done
popd
exit /b %YULIN_EXIT%
