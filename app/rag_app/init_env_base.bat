@echo off
setlocal
REM ============================================
REM  AI知识库 - 基础环境兼容脚本（非首选入口）
REM ============================================

set SCRIPT_DIR=%~dp0
if "%SCRIPT_DIR:~-1%"=="\" set SCRIPT_DIR=%SCRIPT_DIR:~0,-1%
set VENV_DIR=%SCRIPT_DIR%\.venv
set CACHE_DIR=%SCRIPT_DIR%\.cache

echo ========================================
echo   AI知识库 - 基础环境初始化
echo ========================================
echo [DEPRECATED] 该脚本已降级为历史兼容入口，只为旧恢复链保留。
echo [WARN] 该脚本保留为历史兼容入口，优先使用 start.py 和 smoke_test.py 做当前验证。
echo [REDIRECT] 不要把本脚本当成当前主启动入口。
echo.

echo [1/3] 创建虚拟环境到 %VENV_DIR%
if not exist "%VENV_DIR%\Scripts\python.exe" (
    python -m venv "%VENV_DIR%"
    if %errorlevel% neq 0 (
        echo 错误：虚拟环境创建失败，请确认已安装 Python
        pause
        exit /b 1
    )
    echo ✓ 虚拟环境已创建
) else (
    echo 虚拟环境已存在，跳过创建
)

echo [2/3] 激活虚拟环境
call "%VENV_DIR%\Scripts\activate.bat"
if %errorlevel% neq 0 (
    echo 错误：虚拟环境激活失败
    pause
    exit /b 1
)

echo [3/3] 设置缓存路径并安装基础依赖
set HF_HOME=%CACHE_DIR%\huggingface
set PIP_CACHE_DIR=%CACHE_DIR%\pip
pip install -r "%SCRIPT_DIR%\requirements-base.txt" --cache-dir "%PIP_CACHE_DIR%"
if %errorlevel% neq 0 (
    echo 错误：安装基础依赖失败
    pause
    exit /b 1
)
echo ✓ 基础依赖安装完成

echo [REDIRECT] 如需当前执行真值，请回到 docs\HANDOVER.md / docs\ACTIVE_STATE.md。
echo.
pause
