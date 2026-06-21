#Requires AutoHotkey v2.0
#NoTrayIcon
CoordMode "Mouse", "Screen"
hwnd := WinExist("ahk_pid 6416")
if !hwnd
    ExitApp
WinRestore "ahk_id " hwnd
WinActivate "ahk_id " hwnd
DllCall("SetForegroundWindow", "ptr", hwnd)
Sleep 1200
MouseMove 2800, 72, 250
Click
Send "^a{Backspace}"
SendText "文件传输助手"
Sleep 1300
Send "{Enter}"
Sleep 1800
ExitApp
