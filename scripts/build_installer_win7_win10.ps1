param(
    [string]$Version = "",
    [string]$Python38 = "",
    [string]$InnoCompiler = "",
    [string]$EmbeddedPythonZip = "",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue) {
    $Global:PSNativeCommandUseErrorActionPreference = $false
}

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$requirements = Join-Path $root "requirements-win7-win10.txt"
$buildRoot = Join-Path $root "build\installer-win7-win10"
$buildVenv = Join-Path $buildRoot "build-venv"
$buildPython = Join-Path $buildVenv "Scripts\python.exe"
$stageDir = Join-Path $buildRoot "app"
$runtimeRoot = Join-Path $stageDir ".venv"
$runtimeScripts = Join-Path $runtimeRoot "Scripts"
$runtimeSitePackages = Join-Path $runtimeRoot "Lib\site-packages"
$distDir = Join-Path $root "dist\installer-win7-win10"
$cacheDir = Join-Path $root "build\cache"
$embeddedPythonVersion = "3.8.10"
$embeddedPythonName = "python-$embeddedPythonVersion-embed-amd64.zip"
$embeddedPythonUrl = "https://www.python.org/ftp/python/$embeddedPythonVersion/$embeddedPythonName"
$buildPythonPackageUrl = "https://www.nuget.org/api/v2/package/python/$embeddedPythonVersion"
$buildPythonPackageSha256 = "c63a2fc8fc62612b5abd391fda99ae1d90bad42ed8a9e99b1bd81c7ffdb4fefa"
$bootstrapPipName = "pip-24.3.1-py3-none-any.whl"
$bootstrapPipUrl = "https://files.pythonhosted.org/packages/ef/7d/500c9ad20238fcfcb4cb9243eede163594d7020ce87bd9610c9e02771876/pip-24.3.1-py3-none-any.whl"
$bootstrapPipSha256 = "3790624780082365f47549d032f3770eeb2b1e8bd1f7b2e02dace1afa361b4ed"
$iconPng = Join-Path $root "customer_rag\assets\app_icon.png"
$iconIco = Join-Path $stageDir "customer_rag\assets\app_icon.ico"
$innoScript = Join-Path $root "installer\customer-rag.iss"
$logDir = Join-Path $root "logs"
$logPath = Join-Path $logDir "build-installer-win7-win10.log"

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
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $Message"
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

function Resolve-Python38 {
    param([string]$PreferredPath)
    if ($PreferredPath) {
        if (-not (Test-Path -LiteralPath $PreferredPath)) {
            throw "Python 3.8 was not found at: $PreferredPath"
        }
        return (Resolve-Path -LiteralPath $PreferredPath).Path
    }

    $pyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        $candidate = (& $pyLauncher.Source -3.8 -c "import sys; print(sys.executable)" 2>$null | Select-Object -Last 1)
        $launcherExitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorActionPreference
        if ($launcherExitCode -eq 0 -and $candidate -and (Test-Path -LiteralPath $candidate.Trim())) {
            return (Resolve-Path -LiteralPath $candidate.Trim()).Path
        }
    }

    $toolchainRoot = Join-Path $root "build\toolchains\python-$embeddedPythonVersion"
    $toolchainPython = Join-Path $toolchainRoot "tools\python.exe"
    if (Test-Path -LiteralPath $toolchainPython) {
        return (Resolve-Path -LiteralPath $toolchainPython).Path
    }

    Write-Step "Python 3.8 is not installed; downloading the project-local build toolchain..."
    New-Item -ItemType Directory -Force -Path $cacheDir, $toolchainRoot | Out-Null
    $packagePath = Join-Path $cacheDir "python.$embeddedPythonVersion.nupkg"
    $zipPath = Join-Path $cacheDir "python.$embeddedPythonVersion.zip"
    if (-not (Test-Path -LiteralPath $packagePath)) {
        Invoke-WebRequest -UseBasicParsing -Uri $buildPythonPackageUrl -OutFile $packagePath
    }
    $actualPackageHash = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualPackageHash -ne $buildPythonPackageSha256) {
        throw "The Python 3.8 build-tool package failed SHA-256 verification: $packagePath"
    }
    Copy-Item -LiteralPath $packagePath -Destination $zipPath -Force
    Expand-Archive -LiteralPath $zipPath -DestinationPath $toolchainRoot -Force
    if (-not (Test-Path -LiteralPath $toolchainPython)) {
        throw "The project-local Python 3.8 build toolchain could not be prepared."
    }
    return (Resolve-Path -LiteralPath $toolchainPython).Path
}

function Assert-Python38X64 {
    param([Parameter(Mandatory = $true)][string]$PythonPath)
    $description = (& $PythonPath -c "import platform,sys; print('%d.%d|%s' % (sys.version_info[:2] + (platform.architecture()[0],)))").Trim()
    if ($LASTEXITCODE -ne 0 -or $description -ne "3.8|64bit") {
        throw "The compatibility build requires CPython 3.8 x64; found $description at $PythonPath."
    }

    $pythonDll = (& $PythonPath -c "import pathlib,sys; print(pathlib.Path(sys.base_prefix) / 'python38.dll')").Trim()
    if (-not (Test-Path -LiteralPath $pythonDll)) {
        throw "python38.dll was not found beside the selected Python runtime: $pythonDll"
    }
    $dllText = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($pythonDll))
    if ($dllText.Contains("PssQuerySnapshot")) {
        throw "The selected Python 3.8 runtime imports PssQuerySnapshot and cannot run on Windows 7. Use the official CPython 3.8.10 x64 build."
    }
}

function Copy-Tree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string[]]$ExcludeDirs = @(),
        [string[]]$ExcludeFiles = @()
    )
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $arguments = @($Source, $Destination, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    if ($ExcludeDirs.Count -gt 0) {
        $arguments += "/XD"
        $arguments += $ExcludeDirs
    }
    if ($ExcludeFiles.Count -gt 0) {
        $arguments += "/XF"
        $arguments += $ExcludeFiles
    }
    & robocopy @arguments | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy failed from $Source to $Destination with exit code $LASTEXITCODE."
    }
}

function Find-InnoCompiler {
    param([string]$PreferredPath)
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
    foreach ($candidate in @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "D:\Inno Setup 6\ISCC.exe"
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
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
    $includeNames = @("requirements.txt", "requirements-win7-win10.txt")
    $excludeNames = @(
        ".gitignore",
        "build_installer.bat",
        "build_installer_win7_win10.bat",
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
        if ($excludeNames -contains $_.Name) {
            return
        }
        if ($includeNames -contains $_.Name -or $includeExtensions -contains $_.Extension.ToLowerInvariant()) {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $DestinationRoot $_.Name) -Force
        }
    }
}

Write-Step "Build log: $logPath"
if (-not (Test-Path -LiteralPath $requirements)) {
    throw "Missing compatibility requirements file: $requirements"
}

$python38Path = Resolve-Python38 -PreferredPath $Python38
Assert-Python38X64 -PythonPath $python38Path
Write-Step "Using Python 3.8: $python38Path"

# Python 3.8's OpenSSL stack can fail while tunnelling through newer HTTPS
# proxies. PyPI supports direct TLS, so bypass proxies only for its hosts.
$noProxyEntries = @($env:NO_PROXY, "pypi.org", "files.pythonhosted.org") | Where-Object { $_ }
$env:NO_PROXY = $noProxyEntries -join ","
$env:no_proxy = $env:NO_PROXY

Write-Step "Cleaning compatibility build folder..."
Remove-Item -Recurse -Force -LiteralPath $buildRoot -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $buildRoot, $stageDir, $distDir, $cacheDir | Out-Null

Write-Step "Creating isolated Python 3.8 build environment..."
Invoke-Native $python38Path -m venv $buildVenv
$bootstrapPip = Join-Path $cacheDir $bootstrapPipName
if (-not (Test-Path -LiteralPath $bootstrapPip)) {
    Write-Step "Downloading the Python 3.8-compatible pip bootstrap wheel..."
    Invoke-WebRequest -UseBasicParsing -Uri $bootstrapPipUrl -OutFile $bootstrapPip
}
$actualPipHash = (Get-FileHash -LiteralPath $bootstrapPip -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualPipHash -ne $bootstrapPipSha256) {
    throw "The cached pip bootstrap wheel failed SHA-256 verification: $bootstrapPip"
}
Invoke-Native $buildPython -m pip install --disable-pip-version-check --no-index --upgrade $bootstrapPip
Invoke-Native $buildPython -m pip install --disable-pip-version-check "setuptools<76" wheel "pyinstaller==6.16.0"

Write-Step "Installing pinned Win7/Win10 dependencies into the build environment..."
Invoke-Native $buildPython -m pip install --disable-pip-version-check -r $requirements

if (-not $Version) {
    $Version = (& $buildPython -c "from customer_rag import __version__; print(__version__)").Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Could not resolve the CustomerRAG version."
    }
}
Write-Step "Building CustomerRAG $Version for Windows 7 SP1 / Windows 10 x64"

if (-not $EmbeddedPythonZip) {
    $EmbeddedPythonZip = Join-Path $cacheDir $embeddedPythonName
    if (-not (Test-Path -LiteralPath $EmbeddedPythonZip)) {
        Write-Step "Downloading official CPython $embeddedPythonVersion embedded runtime..."
        Invoke-WebRequest -UseBasicParsing -Uri $embeddedPythonUrl -OutFile $EmbeddedPythonZip
    }
} elseif (-not (Test-Path -LiteralPath $EmbeddedPythonZip)) {
    throw "Embedded Python archive was not found at: $EmbeddedPythonZip"
}

Write-Step "Preparing self-contained Python runtime..."
New-Item -ItemType Directory -Force -Path $runtimeScripts, $runtimeSitePackages | Out-Null
Expand-Archive -LiteralPath $EmbeddedPythonZip -DestinationPath $runtimeScripts -Force
$pthPath = Join-Path $runtimeScripts "python38._pth"
if (-not (Test-Path -LiteralPath $pthPath)) {
    throw "The embedded runtime is not CPython 3.8 x64: python38._pth is missing."
}

# On Windows 7, the embedded interpreter can report a corrupt sys.executable
# when launched from the packaged app. In that failure mode python38._pth
# relative entries collapse to the Scripts folder, so startup cannot even find
# the stdlib encodings package. Keep the normal ._pth file, but also unpack the
# stdlib zip directly into Scripts and add a sitecustomize.py that inserts the
# absolute project and site-packages paths using the current working directory.
$stdlibZip = Join-Path $runtimeScripts "python38.zip"
if (Test-Path -LiteralPath $stdlibZip) {
    Expand-Archive -LiteralPath $stdlibZip -DestinationPath $runtimeScripts -Force
}
$siteCustomize = @'
import os
import sys

_roots = []
try:
    _roots.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
except Exception:
    pass
try:
    _roots.append(os.getcwd())
except Exception:
    pass

for _root in _roots:
    _site_packages = os.path.join(_root, '.venv', 'Lib', 'site-packages')
    for _path in (_site_packages, _root):
        if os.path.isdir(_path) and _path not in sys.path:
            sys.path.insert(0, _path)
'@
$siteCustomize | Set-Content -LiteralPath (Join-Path $runtimeScripts "sitecustomize.py") -Encoding ascii
@(
    "python38.zip",
    ".",
    "..\Lib\site-packages",
    "..\..",
    "import site"
) | Set-Content -LiteralPath $pthPath -Encoding ascii

Write-Step "Installing application packages into the self-contained runtime..."
Invoke-Native $buildPython -m pip install --disable-pip-version-check --no-compile --target $runtimeSitePackages -r $requirements

Write-Step "Copying application files..."
Copy-Tree -Source (Join-Path $root "customer_rag") -Destination (Join-Path $stageDir "customer_rag") `
    -ExcludeDirs @("__pycache__") -ExcludeFiles @("*.pyc", "*.pyo")
Copy-Tree -Source (Join-Path $root "wechatExtension") -Destination (Join-Path $stageDir "wechatExtension") `
    -ExcludeDirs @("__pycache__") `
    -ExcludeFiles @("rag-talk-shortcuts.txt", "rag-talk-shortcut-labels.txt", "send-text.txt", "last-selected.txt", "*.log", "*-test-result.ini", "preview-test-result.ini")
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
Invoke-Native $buildPython -c "from pathlib import Path; from PIL import Image; src=Path(r'$iconPng'); dst=Path(r'$iconIco'); dst.parent.mkdir(parents=True, exist_ok=True); Image.open(src).save(dst, sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"

Write-Step "Building the Python 3.8 launcher..."
$pyinstallerWork = Join-Path $buildRoot "pyinstaller"
Invoke-Native $buildPython -m PyInstaller `
    (Join-Path $root "launcher.py") `
    --name CustomerRAG `
    --onefile `
    --noconsole `
    --clean `
    --icon $iconIco `
    --distpath $stageDir `
    --workpath $pyinstallerWork `
    --specpath $pyinstallerWork

$launcherExe = Join-Path $stageDir "CustomerRAG.exe"
if (-not (Test-Path -LiteralPath $launcherExe)) {
    throw "PyInstaller did not create CustomerRAG.exe."
}

Write-Step "Checking the staged Python runtime..."
Push-Location $stageDir
try {
    Invoke-Native (Join-Path $runtimeScripts "python.exe") -c "import sys; assert sys.version_info[:2] == (3, 8); import streamlit, numpy, pandas, pyarrow, faiss, torch; print(sys.version); print('pyarrow', pyarrow.__version__); print('runtime imports ok')"
}
finally {
    Pop-Location
}

$pyarrowDir = Join-Path $runtimeSitePackages "pyarrow"
if (Test-Path -LiteralPath $pyarrowDir) {
    $pyarrowBinaries = Get-ChildItem -LiteralPath $pyarrowDir -Recurse -Include "*.dll", "*.pyd" -File
    foreach ($binary in $pyarrowBinaries) {
        $binaryText = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($binary.FullName))
        if ($binaryText.Contains("api-ms-win-core-synch-l1-2-0.dll")) {
            throw "The packaged pyarrow binary imports api-ms-win-core-synch-l1-2-0.dll, which is not available on Windows 7: $($binary.FullName)"
        }
    }
}

if ($SkipInstaller) {
    Write-Host "Staged Win7/Win10 application: $stageDir"
    Write-Host "Build log: $logPath"
    Stop-Transcript | Out-Null
    exit 0
}

$iscc = Find-InnoCompiler -PreferredPath $InnoCompiler
if (-not $iscc) {
    throw "Inno Setup compiler ISCC.exe was not found. Install Inno Setup 6 or run with -SkipInstaller."
}

Write-Step "Building the Win7/Win10 installer..."
Invoke-Native $iscc `
    "/DMyAppVersion=$Version" `
    "/DSourceDir=$stageDir" `
    "/DOutputDir=$distDir" `
    "/FCustomerRAG-Setup-Win7-Win10-v$Version" `
    $innoScript

Write-Host "Installer output: $distDir"
Write-Host "Build log: $logPath"
Stop-Transcript | Out-Null
