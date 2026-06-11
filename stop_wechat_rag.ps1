$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$wechatStop = Join-Path $root "wechatExtension\stop.ps1"

if (Test-Path $wechatStop) {
    & $wechatStop
}

$ports = @(8501, 8502, 8512)
$listenerPids = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in $ports } |
    Select-Object -ExpandProperty OwningProcess -Unique

$workspacePattern = [regex]::Escape($root)
$workspacePids = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -and
        $_.CommandLine -match $workspacePattern -and
        ($_.CommandLine -match 'launcher\.py|run_streamlit\.py|run_talk_streamlit\.py|customer_rag\.job_worker|llama-server')
    } |
    Select-Object -ExpandProperty ProcessId

@($listenerPids) + @($workspacePids) |
    Where-Object { $_ -and $_ -ne $PID } |
    Sort-Object -Unique |
    ForEach-Object {
        Start-Process -FilePath "taskkill.exe" -ArgumentList "/PID", $_, "/T", "/F" -WindowStyle Hidden -Wait
    }

Write-Host "Customer RAG and all background services have stopped."
