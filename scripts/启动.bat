@echo off
:: AI Knowledge Base - One-click launcher (v3 React SPA)
:: 2026-08-19: goto-style (no nested if blocks); backend runs in THIS window
:: 2026-08-19: port conflict now offers [1] restart / [2] use existing

echo ========================================
echo    AIKB Starting...
echo ========================================

:: already running?
netstat -ano | findstr ":8501" >nul
if not errorlevel 1 goto already_running

:: switch to project root
cd /d "%~dp0.."

:: build frontend only if dist missing
if exist "frontend\dist\index.html" goto start_backend
echo Building frontend (first time)...
cd frontend
call npm run build
if errorlevel 1 goto build_failed
cd ..

:start_backend
set "PYTHONPATH=%CD%"
echo Starting backend... keep this window open.
echo Browser opens in ~18s: http://127.0.0.1:8501
start "" /B cmd /c "timeout /t 18 /nobreak >nul & start http://127.0.0.1:8501"
.venv\Scripts\python.exe app\rag_app\api_server.py
echo.
echo [ERROR] Backend stopped unexpectedly.
pause
exit /b 1

:already_running
echo.
echo Port 8501 is already in use (another AIKB instance may be running).
choice /c 12 /n /m "  [1] Kill old instance and start this version  [2] Use existing service (open browser): "
if errorlevel 2 goto use_existing
cd /d "%~dp0.."
echo Killing old instance...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do (
  echo   Killing PID %%p
  taskkill /F /PID %%p >nul 2>&1
)
echo Old instance stopped. Starting this version...
timeout /t 2 /nobreak >nul
goto start_backend

:use_existing
echo Opening browser...
start http://127.0.0.1:8501
exit /b 0

:build_failed
echo [ERROR] Frontend build failed!
pause
exit /b 1
