# 把已有 ComfyUI 里的模型链接进本项目 models/weights/
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\link_models.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\link_models.ps1 -ComfyModels "D:\ComfyUI\models" -Copy
#
# 默认建硬链接（同一分区不占额外空间）；-Copy 则复制。
param(
    [string]$ComfyModels = "E:\Comfy-Desktop\ComfyUI-Shared\models",
    [switch]$Copy
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $root "models\manifest.json"
$weightsRoot = Join-Path $root "models\weights"

if (-not (Test-Path $manifestPath)) { Write-Host "[ERR] 找不到 models/manifest.json"; exit 1 }
if (-not (Test-Path $ComfyModels)) { Write-Host "[ERR] 找不到 ComfyUI models 目录：$ComfyModels"; exit 1 }

$manifest = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$ok = 0; $skip = 0; $miss = @()

foreach ($m in $manifest.models) {
    $rel = $m.file -replace "/", "\"
    $dest = Join-Path $weightsRoot $rel
    if (Test-Path $dest) { $skip++; continue }

    $src = Join-Path $ComfyModels $rel
    if (-not (Test-Path $src)) { $miss += $m.file; continue }

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    if ($Copy) {
        Copy-Item -LiteralPath $src -Destination $dest
        Write-Host "[COPY] $($m.file)"
    } else {
        try {
            New-Item -ItemType HardLink -Path $dest -Target $src | Out-Null
            Write-Host "[LINK] $($m.file)"
        } catch {
            Copy-Item -LiteralPath $src -Destination $dest
            Write-Host "[COPY] $($m.file)（硬链接失败，已复制）"
        }
    }
    $ok++
}

Write-Host ""
Write-Host "完成：新建 $ok，已存在 $skip，缺失 $($miss.Count)"
if ($miss.Count -gt 0) {
    Write-Host "以下权重在你给的 ComfyUI 目录里没有，需要单独下载："
    $miss | ForEach-Object { Write-Host "  - $_" }
    Write-Host "来源见 models/README.md"
}
