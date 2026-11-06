# 一键推送到 GitHub + Gitee
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\push_all.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\push_all.ps1 -Branch main
#
# 两个 remote 的地址见 `git remote -v`；缺哪个就跳过哪个，不会中断另一个。
param(
    [string]$Branch = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $Branch) {
    $Branch = (git rev-parse --abbrev-ref HEAD).Trim()
}
if (-not $Branch) { Write-Host "[ERR] 取不到当前分支"; exit 1 }

$remotes = git remote
$targets = @()
foreach ($name in @("origin", "gitee")) {
    if ($remotes -contains $name) { $targets += $name }
}
if ($targets.Count -eq 0) { Write-Host "[ERR] 没有配置 origin / gitee remote"; exit 1 }

Write-Host "分支：$Branch"
Write-Host "目标：$($targets -join ', ')"
Write-Host ""

$failed = @()
foreach ($r in $targets) {
    $url = (git remote get-url $r).Trim()
    Write-Host "==> $r  $url"
    git push $r $Branch
    if ($LASTEXITCODE -ne 0) { $failed += $r }
}

Write-Host ""
if ($failed.Count -gt 0) {
    Write-Host "[FAIL] 推送失败：$($failed -join ', ')"
    Write-Host "       常见原因：代理没开（GitHub）、SSH key 失效（Gitee）"
    exit 1
}

Write-Host "[OK] 两个仓库都已更新："
git --no-pager log -1 --oneline
exit 0
