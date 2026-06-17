param(
    [string]$Version = "",
    [string]$InnoCompiler = "",
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
$buildRoot = Join-Path $root "build\installer"
$stageDir = Join-Path $buildRoot "app"
$distDir = Join-Path $root "dist\installer"
$iconPng = Join-Path $root "customer_rag\assets\app_icon.png"
$iconIco = Join-Path $stageDir "customer_rag\assets\app_icon.ico"
$innoScript = Join-Path $root "installer\customer-rag.iss"

if (-not (Test-Path $python)) {
    throw "Missing virtualenv Python: $python"
}

if (-not $Version) {
    $Version = (& $python -c "from customer_rag import __version__; print(__version__)").Trim()
}

function Copy-Tree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string[]]$ExcludeDirs = @(),
        [string[]]$ExcludeFiles = @()
    )
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

Remove-Item -Recurse -Force -LiteralPath $buildRoot -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $stageDir, $distDir | Out-Null

Copy-Tree -Source (Join-Path $root ".venv") -Destination (Join-Path $stageDir ".venv") `
    -ExcludeDirs @("__pycache__", ".pytest_cache") `
    -ExcludeFiles @("*.pyc", "*.pyo")

Copy-Tree -Source (Join-Path $root "customer_rag") -Destination (Join-Path $stageDir "customer_rag") `
    -ExcludeDirs @("__pycache__") `
    -ExcludeFiles @("*.pyc", "*.pyo")

Copy-Tree -Source (Join-Path $root "wechatExtension") -Destination (Join-Path $stageDir "wechatExtension") `
    -ExcludeDirs @("__pycache__") `
    -ExcludeFiles @("rag-talk-shortcuts.txt", "send-text.txt", "last-selected.txt", "*.log", "*-test-result.ini", "preview-test-result.ini")

Copy-RootReleaseFiles -SourceRoot $root -DestinationRoot $stageDir

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
  n_ctx: 4096
  n_threads: 4
  temperature: 0.2
  max_tokens: 512
  num_batch: 128
  keep_alive: 0s
'@
$defaultConfig | Set-Content -LiteralPath (Join-Path $stageDir "config.default.yaml") -Encoding utf8

& $python -c "from pathlib import Path; from PIL import Image; src=Path(r'$iconPng'); dst=Path(r'$iconIco'); dst.parent.mkdir(parents=True, exist_ok=True); Image.open(src).save(dst, sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"

if ($StageOnly) {
    Write-Host "Staged app at: $stageDir"
    exit 0
}

& $python -c "import PyInstaller" *> $null
if ($LASTEXITCODE -ne 0) {
    if ($InstallBuildDeps) {
        & $python -m pip install pyinstaller
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install pyinstaller."
        }
    } else {
        throw "PyInstaller is not installed. Run with -InstallBuildDeps or install pyinstaller in .venv."
    }
}

$pyinstallerWork = Join-Path $buildRoot "pyinstaller"
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
    exit 0
}

$iscc = Find-InnoCompiler -PreferredPath $InnoCompiler
if (-not $iscc) {
    throw "Inno Setup compiler ISCC.exe was not found. Install Inno Setup 6 or run with -SkipInstaller."
}

& $iscc `
    "/DMyAppVersion=$Version" `
    "/DSourceDir=$stageDir" `
    "/DOutputDir=$distDir" `
    $innoScript
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compiler failed with exit code $LASTEXITCODE."
}

Write-Host "Installer output: $distDir"
