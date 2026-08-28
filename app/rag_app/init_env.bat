@echo off
setlocal
REM ============================================
REM  AI知识库 - 历史兼容环境脚本（非首选入口）
REM ============================================

set SCRIPT_DIR=%~dp0
if "%SCRIPT_DIR:~-1%"=="\" set SCRIPT_DIR=%SCRIPT_DIR:~0,-1%
for %%I in ("%SCRIPT_DIR%\..") do set PROJECT_DIR=%%~fI
set VENV_DIR=%SCRIPT_DIR%\.venv
set CACHE_DIR=%SCRIPT_DIR%\.cache

echo.
echo ========================================
echo   AI知识库 - 兼容环境初始化
echo ========================================
echo.
echo [DEPRECATED] 该脚本已降级为历史兼容入口，只为旧恢复链保留。
echo [WARN] 该脚本保留为历史兼容入口，当前更推荐使用:
echo        1. python start.py --health
echo        2. python tests\smoke_test.py
echo        3. docs\HANDOVER.md / docs\项目真值.md
echo [REDIRECT] 不要把本脚本当成当前主启动入口。
echo.

echo [1/4] 创建虚拟环境到 %VENV_DIR%
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

echo [2/4] 激活虚拟环境
call "%VENV_DIR%\Scripts\activate.bat"
if %errorlevel% neq 0 (
    echo 错误：虚拟环境激活失败
    pause
    exit /b 1
)

echo [3/4] 设置缓存到 %CACHE_DIR%
set HF_HOME=%CACHE_DIR%\huggingface
set HF_DATASETS_CACHE=%CACHE_DIR%\huggingface\datasets
set PIP_CACHE_DIR=%CACHE_DIR%\pip
echo ✓ 缓存目录已指向 D 盘项目区

echo [4/4] 安装 Python 依赖
if "%1"=="" (
    set MODE=base
) else (
    set MODE=%1
)

REM 仅支持 base 模式（requirements-base.txt）；ml 模式已废弃（chromadb 为防回退项，禁止安装）
if /I "%MODE%"=="base" (
    set REQ_FILE=%SCRIPT_DIR%\requirements-base.txt
) else (
    echo [WARN] 模式 %MODE% 不支持，回退到 base 模式（ml 已废弃，见 requirements-ml.txt 头部）
    set REQ_FILE=%SCRIPT_DIR%\requirements-base.txt
)

echo 模式: %MODE%
echo 依赖文件: %REQ_FILE%
pip install -r "%REQ_FILE%" --cache-dir "%PIP_CACHE_DIR%"
if %errorlevel% neq 0 (
    echo 错误：依赖安装失败
    pause
    exit /b 1
)
echo ✓ 依赖安装完成

echo.
echo 当前项目根目录: %PROJECT_DIR%
echo 后续建议:
echo   cd /d "%PROJECT_DIR%"
echo   python start.py --health
echo [REDIRECT] 如需当前执行真值，请回到 docs\HANDOVER.md / docs\ACTIVE_STATE.md。
echo.
pause
