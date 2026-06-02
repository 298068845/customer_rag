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

Start-Process -FilePath $ahk -ArgumentList "`"$script`"" -WindowStyle Hidden
Write-Host "WeChat quick tool started."
