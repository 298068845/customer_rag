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
SendText "拾海S030141SD236T1参数"
Send "{Home}+{End}"
Sleep 900
Send "{Tab}"
Sleep 1300
Send "{Tab}"
Sleep 12000
Send "{Tab}"
Sleep 2200
ExitApp
