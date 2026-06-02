$processes = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -like "AutoHotkey*" -and
        $_.CommandLine -like "*WeChatQuickTool.ahk*"
    }

foreach ($process in $processes) {
    Stop-Process -Id $process.ProcessId -Force
}

if ($processes) {
    Write-Host "WeChat quick tool stopped."
} else {
    Write-Host "WeChat quick tool is not running."
}
