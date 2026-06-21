$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ahk = Join-Path $root "tools\autohotkey\AutoHotkey64.exe"
$script = Join-Path $root "WeChatQuickTool.ahk"

if (-not (Test-Path $ahk)) {
    throw "Missing bundled AutoHotkey runtime: $ahk"
}

if (-not (Test-Path $script)) {
    throw "Missing script: $script"
}

# AutoHotkey's #SingleInstance only applies to the same script path. Stop stale
# packaged/development copies first so one global hotkey is handled exactly once.
$existing = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -like "AutoHotkey*" -and
        $_.CommandLine -like "*WeChatQuickTool.ahk*"
    }
foreach ($process in $existing) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
if ($existing) {
    Start-Sleep -Milliseconds 250
}

Start-Process -FilePath $ahk -ArgumentList "`"$script`"" -WindowStyle Hidden
Write-Host "WeChat quick tool started."
