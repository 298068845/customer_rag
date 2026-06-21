#Requires AutoHotkey v2.0
#NoTrayIcon
CoordMode "Mouse", "Screen"
MouseMove 2820, 995, 900
Sleep 1800
MouseMove 3430, 1080, 900
Sleep 1800
MouseMove 3700, 1180, 900
Sleep 1800
Send "{WheelDown 3}"
Sleep 2200
Send "{WheelUp 3}"
Sleep 1800
ExitApp
