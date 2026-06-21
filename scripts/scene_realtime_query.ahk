#Requires AutoHotkey v2.0
#NoTrayIcon
SetTitleMatchMode 2
CoordMode "Mouse", "Screen"
SendLevel 1
hwnd := WinExist("ahk_pid 6416")
if !hwnd
    ExitApp
WinActivate "ahk_id " hwnd
WinRestore "ahk_id " hwnd
DllCall("SetForegroundWindow", "ptr", hwnd)
Sleep 1200
MouseMove 3920, 1280, 0
Click
Sleep 350
Send "^a{Backspace}"
Send "{vkC0}"
Sleep 800
SendText "源氏木语还有卖吗"
Sleep 700
Send "{vkC0}"
Sleep 8000
Send "2"
Sleep 1800
Send "{vkC0}"
Sleep 1600
ExitApp
