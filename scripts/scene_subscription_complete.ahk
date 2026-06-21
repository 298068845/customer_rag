#Requires AutoHotkey v2.0
#NoTrayIcon
SetTitleMatchMode 2
CoordMode "Mouse", "Screen"

hwnd := WinExist("Google Chrome")
if !hwnd
    ExitApp
WinActivate "ahk_id " hwnd
WinWaitActive "ahk_id " hwnd,, 5
Send "^l"
SendText "http://127.0.0.1:8501"
Send "{Enter}"
Sleep 6500

; 打开“导入文件”页，展示已完成的订阅更新任务。
MouseMove 2900, 570, 350
Click
Sleep 2600
Send "{End}"
Sleep 3500
Send "{Home}"
Sleep 1800
ExitApp
