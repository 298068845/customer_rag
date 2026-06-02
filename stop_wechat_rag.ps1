$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$wechatStop = Join-Path $root "wechatExtension\stop.ps1"

if (Test-Path $wechatStop) {
    & $wechatStop
}

Write-Host "Streamlit was left running. Close it manually if you do not need the RAG web UI."
