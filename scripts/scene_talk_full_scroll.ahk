#Requires AutoHotkey v2.0
#NoTrayIcon
CoordMode "Mouse", "Screen"

ScrollPage(count) {
    Loop count {
        Send "{PgDn}"
        Sleep 850
    }
    Send "^{End}"
    Sleep 1800
    Send "^{Home}"
    Sleep 1100
}

; 对话测试
MouseMove 2725, 858, 350
Click
Sleep 1700
ScrollPage(3)

; 实时话术
MouseMove 2825, 858, 350
Click
Sleep 1700
ScrollPage(9)

; 固定话术
MouseMove 2935, 858, 350
Click
Sleep 1700
ScrollPage(9)

; 组合话术
MouseMove 3045, 858, 350
Click
Sleep 1700
Loop 5 {
    Send "{PgDn}"
    Sleep 850
}
Send "^{End}"
Sleep 2200
ExitApp
