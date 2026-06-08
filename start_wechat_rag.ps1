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

$shell = New-Object -ComObject WScript.Shell
$shell.CurrentDirectory = $root
$null = $shell.Run("`"$pythonw`" `"$launcher`"", 0, $false)
Write-Host "Customer RAG launcher started. Use the tray icon in the bottom-right taskbar area."
