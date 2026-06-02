$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$launcher = Join-Path $root "launcher.py"

if (-not (Test-Path $pythonw)) {
    throw "Missing virtualenv Python: $pythonw"
}

if (-not (Test-Path $launcher)) {
    throw "Missing launcher: $launcher"
}

$existing = Get-CimInstance Win32_Process |
    Where-Object {
        ($_.Name -like "python*.exe") -and
        ($_.CommandLine -like "*launcher.py*") -and
        ($_.CommandLine -like "*customer_rag*")
    } |
    Select-Object -First 1

if ($existing) {
    Write-Host "Customer RAG launcher is already running."
    exit 0
}

Start-Process -FilePath $pythonw -ArgumentList "`"$launcher`"" -WorkingDirectory $root -WindowStyle Hidden
Write-Host "Customer RAG launcher started. Use the tray icon in the bottom-right taskbar area."
