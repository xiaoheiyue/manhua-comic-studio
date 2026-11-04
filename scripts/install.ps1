# 一次性安装脚本（Windows，使用 uv）
# 用法： powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#
# 做四件事：用 uv 建 Python 3.12 环境 → 装依赖 → 生成 .env → 拉风格库（可选）→ 跑离线自检
param(
    [switch]$SkipHandraw,
    [switch]$SkipSelfTest
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== 1/5 检查 uv =="
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host "[ERR] 没有找到 uv。先安装（只需一次）："
    Write-Host '      powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'
    Write-Host "      安装后重开一个终端，再跑本脚本。"
    exit 1
}
uv --version

Write-Host "== 2/5 创建虚拟环境（Python 3.12）=="
# .python-version 已固定为 3.12；uv 会自动下载对应 CPython，无需系统预装
uv venv .venv
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "== 3/5 安装依赖 =="
uv pip install -r requirements.txt
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
    & ".\.venv\Scripts\python.exe" selftest.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARN] 自检未通过，请把输出贴到 Issue"
    }
}

Write-Host ""
Write-Host "完成。启动： .\start.ps1"
Write-Host "还需要：本地 ComfyUI 已启动，模型文件见 docs/模型与依赖清单.md"
