param(
    [string]$Version = "",
    [string]$InnoCompiler = "",
    [string]$EmbeddedPythonZip = "",
    [switch]$StageOnly,
    [switch]$SkipInstaller,
    [switch]$InstallBuildDeps
)

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue) {
    $Global:PSNativeCommandUseErrorActionPreference = $false
}

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$python = Join-Path $root ".venv\Scripts\python.exe"
$requirementsLock = Join-Path $root "requirements-win10.lock"
$buildRoot = Join-Path $root "build\installer"
$stageDir = Join-Path $buildRoot "app"
$runtimeDir = Join-Path $stageDir "runtime"
$runtimeSitePackages = Join-Path $runtimeDir "Lib\site-packages"
$distDir = Join-Path $root "dist\installer"
$cacheDir = Join-Path $root "build\cache"
$embeddedPythonVersion = "3.11.9"
$embeddedPythonName = "python-$embeddedPythonVersion-embed-amd64.zip"
$embeddedPythonUrl = "https://www.python.org/ftp/python/$embeddedPythonVersion/$embeddedPythonName"
$embeddedPythonSha256 = "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"
$vcRuntimeFiles = @(
    "concrt140.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "vcruntime140_threads.dll"
)
$iconPng = Join-Path $root "customer_rag\assets\app_icon.png"
$iconIco = Join-Path $stageDir "customer_rag\assets\app_icon.ico"
$innoScript = Join-Path $root "installer\customer-rag.iss"
$logDir = Join-Path $root "logs"
$logPath = Join-Path $logDir "build-installer.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Start-Transcript -Path $logPath -Force | Out-Null
trap {
    Write-Host ""
    Write-Host "Build failed. Log: $logPath"
    try { Stop-Transcript | Out-Null } catch {}
    break
}

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    $timestamp = Get-Date -Format "HH:mm:ss"
    Write-Host "[$timestamp] $Message"
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE."
    }
}

Write-Step "Build log: $logPath"

if (-not (Test-Path $python)) {
    throw "Missing virtualenv Python: $python"
}
if (-not (Test-Path $requirementsLock)) {
    throw "Missing pinned Windows runtime requirements: $requirementsLock"
}

if (-not $Version) {
    Write-Step "Resolving package version..."
    $Version = (& $python -c "from customer_rag import __version__; print(__version__)").Trim()
}
Write-Step "Building CustomerRAG version $Version"

function Copy-Tree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string[]]$ExcludeDirs = @(),
        [string[]]$ExcludeFiles = @()
    )
    Write-Step "Copying $(Split-Path -Leaf $Source) -> $Destination"
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $args = @($Source, $Destination, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    if ($ExcludeDirs.Count -gt 0) {
        $args += "/XD"
        $args += $ExcludeDirs
    }
    if ($ExcludeFiles.Count -gt 0) {
        $args += "/XF"
        $args += $ExcludeFiles
    }
    & robocopy @args | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy failed from $Source to $Destination with exit code $LASTEXITCODE"
    }
}

function Find-InnoCompiler {
    param([string]$PreferredPath = "")
    if ($PreferredPath) {
        if (Test-Path -LiteralPath $PreferredPath) {
            return (Resolve-Path -LiteralPath $PreferredPath).Path
        }
        throw "Inno Setup compiler was not found at: $PreferredPath"
    }
    $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "D:\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }
    return ""
}

function Copy-RootReleaseFiles {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$DestinationRoot
    )
    $includeExtensions = @(".py", ".md", ".txt", ".ps1", ".bat")
    $includeNames = @("requirements.txt")
    $excludeNames = @(
        ".gitignore",
        "build_installer.bat",
        "category_aliases.yaml",
        "config.yaml",
        "config.default.yaml",
        "requirements-build.txt",
        "requirements-win10.lock",
        "llama-server.err.log",
        "llama-server.log",
        "streamlit.err.log",
        "streamlit.log",
        "talk-streamlit.err.log",
        "talk-streamlit.log"
    )

    Get-ChildItem -LiteralPath $SourceRoot -File | ForEach-Object {
        $name = $_.Name
        if ($excludeNames -contains $name) {
            return
        }
        if ($includeNames -contains $name -or $includeExtensions -contains $_.Extension.ToLowerInvariant()) {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $DestinationRoot $name) -Force
        }
    }
}

Write-Step "Cleaning build folder..."
Remove-Item -Recurse -Force -LiteralPath $buildRoot -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $stageDir, $runtimeDir, $runtimeSitePackages, $distDir, $cacheDir | Out-Null

if (-not $EmbeddedPythonZip) {
    $EmbeddedPythonZip = Join-Path $cacheDir $embeddedPythonName
    if (-not (Test-Path -LiteralPath $EmbeddedPythonZip)) {
        Write-Step "Downloading official CPython $embeddedPythonVersion embedded runtime..."
        Invoke-WebRequest -UseBasicParsing -Uri $embeddedPythonUrl -OutFile $EmbeddedPythonZip
    }
} elseif (-not (Test-Path -LiteralPath $EmbeddedPythonZip)) {
    throw "Embedded Python archive was not found at: $EmbeddedPythonZip"
}

$actualRuntimeHash = (Get-FileHash -LiteralPath $EmbeddedPythonZip -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualRuntimeHash -ne $embeddedPythonSha256) {
    throw "Embedded Python SHA-256 mismatch: $EmbeddedPythonZip"
}

Write-Step "Preparing self-contained Python $embeddedPythonVersion runtime..."
Expand-Archive -LiteralPath $EmbeddedPythonZip -DestinationPath $runtimeDir -Force
$pthPath = Join-Path $runtimeDir "python311._pth"
if (-not (Test-Path -LiteralPath $pthPath)) {
    throw "The embedded runtime is not CPython 3.11 x64: python311._pth is missing."
}
@(
    "python311.zip",
    ".",
    "Lib\site-packages",
    "..",
    "import site"
) | Set-Content -LiteralPath $pthPath -Encoding ascii

Write-Step "Adding app-local Microsoft Visual C++ runtime..."
foreach ($runtimeFile in $vcRuntimeFiles) {
    $sourceRuntimeFile = Join-Path $env:WINDIR "System32\$runtimeFile"
    if (-not (Test-Path -LiteralPath $sourceRuntimeFile)) {
        throw "Missing $runtimeFile. Install the Microsoft Visual C++ 2015-2022 x64 Redistributable on the build machine."
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $sourceRuntimeFile
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notlike "*O=Microsoft Corporation*") {
        throw "Refusing to package an unsigned or non-Microsoft runtime file: $sourceRuntimeFile"
    }
    Copy-Item -LiteralPath $sourceRuntimeFile -Destination (Join-Path $runtimeDir $runtimeFile) -Force
}
$vcRuntimeVersion = (Get-Item (Join-Path $runtimeDir "msvcp140.dll")).VersionInfo.FileVersion
Write-Step "Using app-local Microsoft Visual C++ runtime $vcRuntimeVersion"

Write-Step "Installing pinned application packages into the self-contained runtime..."
Invoke-Native $python -m pip install `
    --disable-pip-version-check `
    --only-binary=:all: `
    --no-compile `
    --target $runtimeSitePackages `
    -r $requirementsLock

# pip writes console-script wrappers under site-packages\bin with a shebang that
# points at the build interpreter. The application invokes modules directly and
# does not need these non-portable wrappers.
Remove-Item -LiteralPath (Join-Path $runtimeSitePackages "bin") -Recurse -Force -ErrorAction SilentlyContinue

Copy-Tree -Source (Join-Path $root "customer_rag") -Destination (Join-Path $stageDir "customer_rag") `
    -ExcludeDirs @("__pycache__") `
    -ExcludeFiles @("*.pyc", "*.pyo")

Copy-Tree -Source (Join-Path $root "wechatExtension") -Destination (Join-Path $stageDir "wechatExtension") `
    -ExcludeDirs @("__pycache__") `
    -ExcludeFiles @("rag-talk-shortcuts.txt", "rag-talk-shortcut-labels.txt", "send-text.txt", "last-selected.txt", "*.log", "*-test-result.ini", "preview-test-result.ini")

Copy-RootReleaseFiles -SourceRoot $root -DestinationRoot $stageDir

Write-Step "Writing default installer config..."
$defaultConfig = @'
raw_data_dir: ''
index_dir: ''
talk_data_dir: ''
embedding_model_path: ''
llm_model_path: ''
tools_dir: ''
chunk_size: 700
chunk_overlap: 120
embedding_batch_size: 32
top_k: 5
search_timeout_seconds: 4
product_search_timeout_seconds: 6
precise_search_timeout_seconds: 8
llm:
  backend: llama_cpp_server
  ollama_url: http://localhost:11434
  ollama_model: deepseek-r1:1.5b
  llama_server_url: http://127.0.0.1:8081
  llama_server_host: 127.0.0.1
  llama_server_port: 8081
  llama_server_executable: ''
  llama_server_backend: auto
  n_gpu_layers: 0
  n_parallel: 1
  prompt_cache_mb: 1024
  n_ctx: 4096
  n_threads: 4
  temperature: 0.2
  max_tokens: 512
  num_batch: 128
  keep_alive: 0s
'@
$defaultConfig | Set-Content -LiteralPath (Join-Path $stageDir "config.default.yaml") -Encoding utf8

Write-Step "Generating Windows icon..."
Invoke-Native $python -c "from pathlib import Path; from PIL import Image; src=Path(r'$iconPng'); dst=Path(r'$iconIco'); dst.parent.mkdir(parents=True, exist_ok=True); Image.open(src).save(dst, sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"

Write-Step "Checking staged self-contained runtime..."
Push-Location $stageDir
try {
    Invoke-Native (Join-Path $runtimeDir "python.exe") -c "import sys; assert sys.version_info[:2] == (3, 11); import cryptography, faiss, numpy, pandas, pyarrow, pystray, streamlit, torch, win32api; import customer_rag; print(sys.executable); print('runtime imports ok')"
    Invoke-Native (Join-Path $runtimeDir "python.exe") (Join-Path $stageDir "customer_rag\wechat_bridge.py") --help
}
finally {
    Pop-Location
}

Write-Step "Auditing staged release paths..."
$stagedWechatScript = Join-Path $stageDir "wechatExtension\WeChatQuickTool.ahk"
$stagedWechatSource = Get-Content -LiteralPath $stagedWechatScript -Raw
if ($stagedWechatSource -notmatch 'python\s*:=\s*projectRoot\s*"\\runtime\\python\.exe"') {
    throw "The staged WeChat plugin does not prefer the bundled Python runtime."
}

$releaseTextExtensions = @(".ahk", ".bat", ".ini", ".json", ".md", ".ps1", ".py", ".txt", ".yaml", ".yml")
$developmentPathPattern = '(?i)(?:[A-Z]:\\(?:Users|workplace)\\|ProgramData\\anaconda|AppData\\Local\\Programs\\Python)'
$developmentPathLeaks = Get-ChildItem -LiteralPath $stageDir -Recurse -File | Where-Object {
    -not $_.FullName.StartsWith($runtimeDir, [System.StringComparison]::OrdinalIgnoreCase) -and
    $releaseTextExtensions -contains $_.Extension.ToLowerInvariant()
} | Select-String -Pattern $developmentPathPattern
if ($developmentPathLeaks) {
    $details = ($developmentPathLeaks | ForEach-Object { "$($_.Path):$($_.LineNumber): $($_.Line.Trim())" }) -join [Environment]::NewLine
    throw "Development-machine paths were found in the staged release:$([Environment]::NewLine)$details"
}

if ($StageOnly) {
    Write-Host "Staged app at: $stageDir"
    Stop-Transcript | Out-Null
    exit 0
}

Write-Step "Checking PyInstaller..."
& $python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
if ($LASTEXITCODE -ne 0) {
    if ($InstallBuildDeps) {
        Write-Step "Installing PyInstaller into the build environment..."
        & $python -m pip install pyinstaller
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install pyinstaller."
        }
    } else {
        throw "PyInstaller is not installed. Run with -InstallBuildDeps or install it in the build environment."
    }
}

$pyinstallerWork = Join-Path $buildRoot "pyinstaller"
Write-Step "Building launcher executable with PyInstaller..."
& $python -m PyInstaller `
    (Join-Path $root "launcher.py") `
    --name CustomerRAG `
    --onefile `
    --noconsole `
    --clean `
    --icon $iconIco `
    --distpath $stageDir `
    --workpath $pyinstallerWork `
    --specpath $pyinstallerWork

if (-not (Test-Path (Join-Path $stageDir "CustomerRAG.exe"))) {
    throw "PyInstaller did not create CustomerRAG.exe"
}

if ($SkipInstaller) {
    Write-Host "Staged app at: $stageDir"
    Stop-Transcript | Out-Null
    exit 0
}

Write-Step "Locating Inno Setup compiler..."
$iscc = Find-InnoCompiler -PreferredPath $InnoCompiler
if (-not $iscc) {
    throw "Inno Setup compiler ISCC.exe was not found. Install Inno Setup 6 or run with -SkipInstaller."
}

Write-Step "Building installer with Inno Setup. This can take several minutes..."
& $iscc `
    "/DMyAppVersion=$Version" `
    "/DSourceDir=$stageDir" `
    "/DOutputDir=$distDir" `
    $innoScript
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compiler failed with exit code $LASTEXITCODE."
}

Write-Host "Installer output: $distDir"
Write-Host "Build log: $logPath"
Stop-Transcript | Out-Null
