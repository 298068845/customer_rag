#Requires AutoHotkey v2.0
#NoTrayIcon
CoordMode "Mouse", "Screen"

; 导入文件页
Send "^{Home}"
Sleep 1200
MouseMove 2900, 570, 350
Click
Sleep 1800

; 开始后台更新
MouseMove 2820, 995, 550
Click
Sleep 13000
ExitApp
