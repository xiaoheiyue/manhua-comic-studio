# 一次性安装脚本（Windows）
# 用法： powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#
# 做四件事：建虚拟环境 → 装依赖 → 生成 .env → 拉风格库（可选）→ 跑离线自检
param(
    [switch]$SkipHandraw,
    [switch]$SkipSelfTest
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== 1/5 检查 Python =="
$python = "python"
try { & $python --version } catch {
    Write-Host "[ERR] 找不到 python，请先安装 Python 3.10-3.12 并加入 PATH"
    exit 1
}

Write-Host "== 2/5 创建虚拟环境 .venv =="
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $python -m venv .venv
} else {
    Write-Host "已存在，跳过"
}
$py = ".\.venv\Scripts\python.exe"

Write-Host "== 3/5 安装依赖 =="
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "== 4/5 配置 .env 与风格库 =="
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[TODO] 已生成 .env，请编辑并填入 DEEPSEEK_API_KEY"
} else {
    Write-Host ".env 已存在，跳过"
}
if (-not $SkipHandraw) {
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "fetch_handraw_style.ps1")
}

Write-Host "== 5/5 离线自检 =="
if (-not $SkipSelfTest) {
    & $py selftest.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARN] 自检未通过，请把输出贴到 Issue"
    }
}

Write-Host ""
Write-Host "完成。启动： .\start.ps1   （或 python server.py）"
Write-Host "还需要：本地 ComfyUI 已启动，模型文件见 docs/模型与依赖清单.md"
