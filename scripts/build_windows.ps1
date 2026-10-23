# W-030: Windows build chain for the current UI stack (PyInstaller onedir + windowed).
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows.ps1 `
#     -WorkspaceRoot "E:\FootballSimulator" `
#     -PythonExe "E:\FootballSimulator\.venv-win\Scripts\python.exe" `
#     -OutputDir "E:\FootballSimulator\release\windows"
#
# Guarantees:
# - every cache/temp/output path stays inside -WorkspaceRoot (validated, no reparse points);
# - no global conda env, no system settings, no installers;
# - each external command's exit code is checked;
# - version resource and artifact names come from football_simulator/version.py.
param(
    # Defaults to the parent of this script's directory (source\ -> workspace).
    [string]$WorkspaceRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$OutputDir = "",
    [string]$SourceDir = "",
    [switch]$CandidateOnly,
    [switch]$SkipDeps
)
$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) { Write-Host "==> $Message" }
function Fail([string]$Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

# PowerShell 5.1 turns a native command's stderr into a terminating error when
# $ErrorActionPreference = "Stop"; pip/PyInstaller write progress to stderr.
# Run them with "Continue", capture all streams to a log, and judge by exit code.
function Invoke-Captured([string]$Exe, [string[]]$Arguments, [string]$LogPath) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @Arguments *> $LogPath
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    return $code
}


# ---------- paths and boundary checks ----------
if (-not (Test-Path -LiteralPath $WorkspaceRoot)) { Fail "workspace not found: $WorkspaceRoot" }
$Workspace = (Get-Item -LiteralPath $WorkspaceRoot).FullName.TrimEnd('\')
if ($Workspace.Length -le 3) { Fail "refusing to use a drive root as workspace: $Workspace" }

if (-not $SourceDir) { $SourceDir = Join-Path $Workspace "source" }
if (-not $OutputDir) { $OutputDir = Join-Path $Workspace "release\windows" }
if (-not $PythonExe) { $PythonExe = Join-Path $Workspace ".venv-win\Scripts\python.exe" }

$Source = (Get-Item -LiteralPath $SourceDir).FullName.TrimEnd('\')
$BuildDir = Join-Path $Workspace "build"
$DistDir = Join-Path $Workspace "dist"
$TmpDir = Join-Path $Workspace ".tmp"
$StagingDir = Join-Path $BuildDir "staging"
$Artifacts = Join-Path $Workspace "artifacts\windows"
$SpecPath = Join-Path $Source "Football Simulator UI v2 Windows.spec"
$VersionFile = Join-Path $BuildDir "windows_version_info.txt"

foreach ($p in @($Source, $OutputDir, $BuildDir, $DistDir, $TmpDir, $Artifacts)) {
    if (-not $p.StartsWith($Workspace + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        Fail "path escapes workspace: $p"
    }
}
if (-not (Test-Path -LiteralPath $SpecPath)) { Fail "spec not found: $SpecPath" }
if (-not (Test-Path -LiteralPath $PythonExe)) { Fail "python not found: $PythonExe (run W-020 env setup first)" }

# Refuse to operate through reparse points (junction/symlink to outside the workspace).
foreach ($p in @($Source, $BuildDir, $DistDir, $TmpDir)) {
    if (Test-Path -LiteralPath $p) {
        $item = Get-Item -LiteralPath $p -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { Fail "reparse point refused: $p" }
    }
}

# ---------- process-scoped environment: no user/system changes ----------
$env:TEMP = $TmpDir
$env:TMP = $TmpDir
$env:PIP_CACHE_DIR = Join-Path $Workspace ".cache\pip"
$env:PYINSTALLER_CONFIG_DIR = Join-Path $Workspace ".cache\pyinstaller"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONNOUSERSITE = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONPYCACHEPREFIX = Join-Path $Workspace ".cache\pycache"
$env:FS_WINDOWS_VERSION_FILE = $VersionFile
$env:QT_QPA_PLATFORM = "offscreen"

New-Item -ItemType Directory -Force -Path $BuildDir, $DistDir, $TmpDir, $Artifacts | Out-Null
New-Item -ItemType Directory -Force -Path $env:PYINSTALLER_CONFIG_DIR | Out-Null

# ---------- interpreter / dependency checks ----------
Write-Step "interpreter check"
$probe = & $PythonExe -c "import json,platform,sys,sysconfig;print(json.dumps({'version':platform.python_version(),'bits':64 if sys.maxsize>2**32 else 32,'platform':sysconfig.get_platform()}))"
if ($LASTEXITCODE -ne 0) { Fail "python probe failed" }
Write-Host $probe
$probeJson = $probe | ConvertFrom-Json
if ($probeJson.bits -ne 64) { Fail "need a 64-bit interpreter, got $($probeJson.bits)-bit" }

if (-not $SkipDeps) {
    Write-Step "dependency check"
    & $PythonExe -c "import PySide6, PyInstaller, shiboken6; print('PySide6', PySide6.__version__, 'PyInstaller', PyInstaller.__version__, 'shiboken6', shiboken6.__version__)"
    if ($LASTEXITCODE -ne 0) { Fail "PySide6/PyInstaller missing in $PythonExe (run W-020 env setup; this script never creates a global env)" }
}

# ---------- version resource (single source of truth) ----------
Write-Step "generate version resource from football_simulator/version.py"
& $PythonExe (Join-Path $Source "scripts\write_windows_version_info.py") --root $Source --output $VersionFile
if ($LASTEXITCODE -ne 0) { Fail "version resource generation failed" }
if (-not (Test-Path -LiteralPath $VersionFile)) { Fail "version file missing: $VersionFile" }

# ---------- clean only known build dirs ----------
Write-Step "clean previous build output"
$pyiWork = Join-Path $BuildDir "pyinstaller"
foreach ($dir in @($pyiWork, $DistDir, $StagingDir)) {
    if (Test-Path -LiteralPath $dir) {
        $resolved = (Resolve-Path -LiteralPath $dir).Path
        if (-not $resolved.StartsWith($Workspace + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
            Fail "refusing to delete outside workspace: $resolved"
        }
        Remove-Item -LiteralPath $dir -Recurse -Force
    }
}
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

# ---------- PyInstaller ----------
Write-Step "run PyInstaller (onedir, windowed)"
$pyiLog = Join-Path $Artifacts "pyinstaller-build.log"
Push-Location $Source
try {
    $pyiArgs = @("-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "INFO", "--distpath", $DistDir, "--workpath", $pyiWork, $SpecPath)
    $pyiExit = Invoke-Captured $PythonExe $pyiArgs $pyiLog
} finally {
    Pop-Location
}
if ($pyiExit -ne 0) {
    Get-Content -LiteralPath $pyiLog -Tail 40 | Write-Host
    Fail "PyInstaller failed with exit code $pyiExit (log: $pyiLog)"
}

$AppDir = Join-Path $DistDir "Football Simulator"
$ExePath = Join-Path $AppDir "Football Simulator.exe"
if (-not (Test-Path -LiteralPath $ExePath)) { Fail "missing exe: $ExePath" }
$InternalDir = Join-Path $AppDir "_internal"
if (-not (Test-Path -LiteralPath $InternalDir)) { Fail "missing _internal dir (onedir layout required): $InternalDir" }
$qwindows = Get-ChildItem -LiteralPath $InternalDir -Recurse -Filter "qwindows.dll" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $qwindows) { Fail "qwindows.dll not bundled under _internal" }

$exeItem = Get-Item -LiteralPath $ExePath
$exeVersion = $exeItem.VersionInfo.FileVersion
$exeProduct = $exeItem.VersionInfo.ProductVersion
Write-Host ("exe={0} bytes fileVersion={1} productVersion={2}" -f $exeItem.Length, $exeVersion, $exeProduct)

# ---------- assemble portable ZIP ----------
Write-Step "assemble portable ZIP"
$assembleArgs = @(
    (Join-Path $Source "scripts\assemble_windows_package.py"),
    "--workspace", $Workspace,
    "--source", $Source,
    "--dist", $DistDir,
    "--staging", $StagingDir,
    "--output", $OutputDir
)
$manifestPath = Join-Path $Workspace "artifacts\windows\source-manifest.json"
if (Test-Path -LiteralPath $manifestPath) { $assembleArgs += @("--manifest", $manifestPath) }
if ($CandidateOnly) { $assembleArgs += "--candidate-only" }
$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $assembleOut = & $PythonExe @assembleArgs 2>&1
    $assembleCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previous
}
if ($assembleCode -ne 0) {
    Write-Host ($assembleOut | Out-String)
    Fail "package assembly failed (exit $assembleCode)"
}
$assembleJson = ($assembleOut | Out-String)
$assembleSummaryPath = Join-Path $Artifacts "w030-package.json"
$assembleJson | Set-Content -LiteralPath $assembleSummaryPath -Encoding UTF8
Write-Host $assembleJson

Write-Host "BUILD_OK"
