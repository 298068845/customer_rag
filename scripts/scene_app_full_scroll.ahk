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

; 问答
MouseMove 2685, 570, 350
Click
Sleep 1800
ScrollPage(3)

; 语料管理
MouseMove 2790, 570, 350
Click
Sleep 1800
ScrollPage(7)

; 导入文件 / 订阅管理
MouseMove 2900, 570, 350
Click
Sleep 1800
ScrollPage(5)

; Prompt 设置
MouseMove 3030, 570, 350
Click
Sleep 1800
ScrollPage(5)

; 商品类目
MouseMove 3160, 570, 350
Click
Sleep 1800
Loop 7 {
    Send "{PgDn}"
    Sleep 850
}
Send "^{End}"
Sleep 2200
ExitApp
