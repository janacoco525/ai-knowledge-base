# AI知识库 — Windows 服务化脚本
# 使用方式：以管理员身份运行 PowerShell，执行 .\scripts\install-service.ps1
# 需要: 安装 nssm (https://nssm.cc/) 或使用计划任务方案

Write-Host "AI知识库 — 安装为 Windows 计划任务（开机自启 + 每分钟保活）" -ForegroundColor Cyan

$projectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pythonPath = Join-Path $projectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonPath)) { $pythonPath = (Get-Command python).Source }
$apiServer = Join-Path $projectDir "app\rag_app\api_server.py"
$taskName = "AI知识库-服务"

# 删除旧任务
schtasks /delete /tn $taskName /f 2>$null

# 创建新任务：登录时启动 + 每2分钟保活检查
schtasks /create /tn $taskName `
    /tr "powershell -WindowStyle Hidden -Command { `$port = (Get-NetTCPConnection -LocalPort 8501 -ErrorAction SilentlyContinue); if (-not `$port) { Start-Process -FilePath '$pythonPath' -ArgumentList '$apiServer' -WindowStyle Hidden } }" `
    /sc minute /mo 2 /f

Write-Host "✅ 已安装计划任务: $taskName" -ForegroundColor Green
Write-Host "   触发: 每分钟检查8501端口，未运行则自动启动" -ForegroundColor Green
Write-Host "   卸载: schtasks /delete /tn '$taskName' /f" -ForegroundColor Yellow
