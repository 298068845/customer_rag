#Requires AutoHotkey v2.0
#SingleInstance Force

SetTitleMatchMode 2
CoordMode "Mouse", "Screen"

action := A_Args.Length ? A_Args[1] : ""

ActivateWeChat() {
    hwnd := WinExist("文件传输助手 ahk_exe Weixin.exe")
    if !hwnd {
        hwnd := WinExist("ahk_exe Weixin.exe")
    }
    if !hwnd {
        return false
    }
    WinActivate "ahk_id " hwnd
    WinWaitActive "ahk_id " hwnd,, 5
    return true
}

FocusWeChatInput() {
    MouseMove 3920, 1280, 0
    Click
    Sleep 350
}

OpenChrome(url) {
    Run '"C:\Program Files\Google\Chrome\Application\chrome.exe" --new-window --start-maximized "' url '"'
    WinWait "ahk_exe chrome.exe",, 10
    WinActivate "ahk_exe chrome.exe"
    WinWaitActive "ahk_exe chrome.exe",, 5
    Sleep 6500
}

if action = "app" {
    OpenChrome("http://127.0.0.1:8501")
} else if action = "talk" {
    OpenChrome("http://127.0.0.1:8502")
} else if action = "wechat" {
    ActivateWeChat()
} else if action = "click" && A_Args.Length >= 3 {
    MouseMove Integer(A_Args[2]), Integer(A_Args[3]), 0
    Click
    Sleep 1800
} else if action = "escape" {
    Send "{Esc}"
    Sleep 800
} else if action = "subscription_scene" {
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
} else if action = "talk_scene" {
    MouseMove 2825, 858, 600
    Click
    Sleep 3200
    MouseMove 2935, 858, 600
    Click
    Sleep 3200
    MouseMove 3045, 858, 600
    Click
    Sleep 4200
} else if action = "tab_query" {
    if ActivateWeChat() {
        FocusWeChatInput()
        SendText "拾海S030141SD236T1"
        Send "^a"
        Sleep 900
        Send "{Tab}"
        Sleep 1300
        Send "{Tab}"
        Sleep 9000
        Send "{Tab}"
        Sleep 2200
        Send "{Esc}"
    }
} else if action = "realtime_query" {
    if ActivateWeChat() {
        FocusWeChatInput()
        Send "{vkC0}"
        Sleep 800
        SendText "源氏木语还有卖吗"
        Sleep 700
        Send "{vkC0}"
        Sleep 4500
        Send "2"
        Sleep 1800
        Send "{vkC0}"
        Sleep 1600
        Send "{Esc}"
    }
} else if action = "combined_query" {
    if ActivateWeChat() {
        FocusWeChatInput()
        Send "{vkC0}"
        Sleep 800
        SendText "检索1"
        Sleep 700
        Send "{vkC0}"
        Sleep 4500
        Send "1"
        Sleep 1800
        Send "{vkC0}"
        Sleep 1400
        Send "{vkC0}"
        Sleep 1400
        Send "{vkC0}"
        Sleep 1800
        Send "{Esc}"
}

ExitApp
