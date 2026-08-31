# Windows 一键构建脚本：conda 环境 + PyInstaller + 组装 release zip。
# 用法（在仓库根目录）：
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
# 或在根目录双击 build_windows_ui_v2.bat（等价调用）。

param(
    [string]$EnvName = "football-sim-build",
    [string]$PythonVersion = "3.11",
    [switch]$SkipEnvSetup
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Fail([string]$Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

# ---------- 定位 conda ----------
$condaExe = $null
if ($env:CONDA_EXE -and (Test-Path -LiteralPath $env:CONDA_EXE)) {
    $condaExe = $env:CONDA_EXE
} else {
    $cmd = Get-Command conda -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.CommandType -eq "Application" -and $cmd.Source -and (Test-Path -LiteralPath $cmd.Source)) {
        $condaExe = $cmd.Source
    } else {
        $cmd = Get-Command conda.bat -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -and (Test-Path -LiteralPath $cmd.Source)) {
            $condaExe = $cmd.Source
        }
    }
}
if (-not $condaExe) {
    Fail "未找到 conda。请先安装 Miniconda/Anaconda，并从 Anaconda Prompt（conda 已初始化）运行本脚本。"
}
Write-Step "使用 conda: $condaExe"

function Invoke-Conda([string[]]$ArgsList) {
    & $condaExe run -n $EnvName @ArgsList
    if ($LASTEXITCODE -ne 0) {
        Fail "conda run '$($ArgsList -join ' ')' 失败（exit code $LASTEXITCODE）。"
    }
}

# ---------- 确保构建环境存在 ----------
& $condaExe run -n $EnvName python --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Step "创建 conda 环境 '$EnvName'（python $PythonVersion）"
    & $condaExe create -y -n $EnvName "python=$PythonVersion"
    if ($LASTEXITCODE -ne 0) {
        Fail "conda create 失败。"
    }
}

if (-not $SkipEnvSetup) {
    Write-Step "安装/更新依赖"
    Invoke-Conda @("python", "-m", "pip", "install", "--upgrade", "pip")
    Invoke-Conda @("python", "-m", "pip", "install", "-r", "requirements-ui-v2.txt")
}

# ---------- 准备英文副本配置（运行时按 UTF-8 中文名优先，英文名兜底） ----------
$zhConfig = Join-Path $Root "足球模拟器总配置.json"
$enConfig = Join-Path $Root "football_simulator_config.json"
if (-not (Test-Path -LiteralPath $enConfig)) {
    if (-not (Test-Path -LiteralPath $zhConfig)) {
        Fail "缺少配置文件: $zhConfig"
    }
    Copy-Item -LiteralPath $zhConfig -Destination $enConfig
    Write-Step "已生成英文配置副本: $enConfig"
}

# ---------- 清理旧构建 ----------
Write-Step "清理旧构建目录"
foreach ($dir in @("build-windows-ui-v2", "dist-windows-ui-v2")) {
    $p = Join-Path $Root $dir
    if (Test-Path -LiteralPath $p) {
        Remove-Item -LiteralPath $p -Recurse -Force
    }
}

# ---------- PyInstaller ----------
Write-Step "运行 PyInstaller（可能需要几分钟）"
Invoke-Conda @(
    "python", "-m", "PyInstaller",
    "--noconfirm", "--clean",
    "--distpath", "dist-windows-ui-v2",
    "--workpath", "build-windows-ui-v2",
    "Football Simulator UI v2 Windows.spec"
)

# ---------- 组装 release 目录并压缩 ----------
Write-Step "组装 release 产物"
$distApp = Join-Path $Root "dist-windows-ui-v2Football Simulator UI v2"
if (-not (Test-Path -LiteralPath $distApp)) {
    Fail "未找到构建产物: $distApp"
}

$releaseDir = Join-Path $Root "releasewindowsFootball-Simulator-UI-v2-Windows"
$zipPath = Join-Path $Root "releasewindowsFootball-Simulator-UI-v2-Windows.zip"

if (Test-Path -LiteralPath $releaseDir) {
    Remove-Item -LiteralPath $releaseDir -Recurse -Force
}
New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
Get-ChildItem -LiteralPath $distApp | Copy-Item -Destination $releaseDir -Recurse -Force
Copy-Item -LiteralPath (Join-Path $Root "releasewindowsREADME.md") -Destination $releaseDir -Force
Copy-Item -LiteralPath $zhConfig -Destination $releaseDir -Force
Copy-Item -LiteralPath $enConfig -Destination $releaseDir -Force

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path $releaseDir -DestinationPath $zipPath -Force

Write-Host ""
Write-Host "构建完成" -ForegroundColor Green
Write-Host "  exe : $distAppFootball Simulator UI v2.exe"
Write-Host "  zip : $zipPath"
