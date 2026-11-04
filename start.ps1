# 启动脚本（Windows）
# 用法： .\start.ps1
# 会优先使用 .venv 里的 Python；没有就退回系统 python
Set-Location (Split-Path -Parent $PSScriptRoot)

if (Test-Path ".venv\Scripts\python.exe") {
    $py = ".\.venv\Scripts\python.exe"
} elseif (Test-Path "env\Scripts\python.exe") {
    $py = ".\env\Scripts\python.exe"
} else {
    $py = "python"
}

Write-Host "使用解释器：$py"
Write-Host "启动后请打开 http://127.0.0.1:7870"
& $py server.py
