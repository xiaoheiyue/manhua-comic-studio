# 拉取 handraw-style 风格库（365 画风 / 170 版式 / 36 主题色）
#
# handraw-style 体积约 400MB，不随本仓库提交。首次运行前执行：
#   powershell -ExecutionPolicy Bypass -File scripts\fetch_handraw_style.ps1
#
# 许可：MIT + 署名要求（作者 yang0，https://github.com/yang0/handraw-style）
param(
    [string]$Dest = (Join-Path (Split-Path -Parent $PSScriptRoot) "handraw-style")
)

$ErrorActionPreference = "Stop"
$marker = Join-Path $Dest "skills\handdraw-style-prompter\references\styles.json"

if (Test-Path $marker) {
    Write-Host "[OK] handraw-style 已存在：$Dest"
    exit 0
}

if (Test-Path $Dest) {
    Write-Host "[WARN] 目录已存在但不完整，先删除再重试：$Dest"
    Write-Host "       Remove-Item -Recurse -Force '$Dest'"
    exit 1
}

Write-Host "[1/2] git clone --depth 1 https://github.com/yang0/handraw-style.git"
git clone --depth 1 https://github.com/yang0/handraw-style.git $Dest
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERR] clone 失败。可手动下载 zip 解压到 $Dest"
    exit 1
}

Write-Host "[2/2] 校验"
if (-not (Test-Path $marker)) {
    Write-Host "[ERR] 缺少 $marker，目录不完整"
    exit 1
}
Write-Host "[OK] 完成。保留 LICENSE 与署名：yang0 / https://github.com/yang0/handraw-style"
