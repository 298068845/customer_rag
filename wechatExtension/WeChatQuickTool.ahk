#Requires AutoHotkey v2.0
#SingleInstance Force
#NoTrayIcon

SetTitleMatchMode 2
CoordMode "Mouse", "Screen"
CoordMode "ToolTip", "Screen"

global CONFIG_PATH := A_ScriptDir "\config.ini"
global SEND_TEXT_PATH := A_ScriptDir "\send-text.txt"
global LAST_SELECTED_PATH := A_ScriptDir "\last-selected.txt"
global PREVIEW_TEST_PATH := A_ScriptDir "\preview-test-result.ini"
global CUSTOM_TAB_PATHS := [
    A_ScriptDir "\custom-tab-1.txt",
    A_ScriptDir "\custom-tab-2.txt",
    A_ScriptDir "\custom-tab-3.txt",
    A_ScriptDir "\custom-tab-4.txt",
    A_ScriptDir "\custom-tab-5.txt"
]

global PREVIEW_GUI := 0
global PREVIEW_VISIBLE := false
global PREVIEW_TARGET_HWND := 0
global PREVIEW_SOURCE_TEXT := ""
global PREVIEW_PARTS := []
global PREVIEW_TAB_TEXTS := []
global PREVIEW_TAB_DEFAULT_CHECKED := true
global PREVIEW_ACTIVE_TAB := 1
global PREVIEW_INDEX := 1
global PREVIEW_EDIT := 0
global PREVIEW_LIST := 0
global PREVIEW_STATUS := 0
global PREVIEW_TABS := 0
global PREVIEW_SHOW_TABS := false
global PREVIEW_MODE_ALL := 0
global PREVIEW_MODE_SPLIT := 0
global PREVIEW_CLOSE_BUTTON := 0
global LAST_SEND_TICK := 0
global MIN_SEND_INTERVAL_MS := 300
global STATUS_GUI := 0
global STATUS_TITLE := 0
global STATUS_BODY := 0
global RAG_QUERY_PID := 0
global STATUS_TEST_FORCE_MONITOR := 0

EnsureBootstrapFiles()

if HasArg("--preview-test") {
    RunPreviewSelfTest()
    ExitApp
}

if HasArg("--status-test") {
    RunStatusSelfTest()
    ExitApp
}

if HasArg("--clipboard-file-test") {
    RunClipboardFileSelfTest()
    ExitApp
}

if HasArg("--clipboard-image-test") {
    RunClipboardImageSelfTest()
    ExitApp
}

#HotIf IsWeChatActive()
Tab::HandleTabHotkey()
vkC0::PreviewOrSendNext()
F8::CalibrateSendBoxPoint()
#HotIf

#HotIf IsPreviewVisible()
Tab::SendNextPreviewPart()
vkC0::PreviewOrSendNext()
Esc::ClosePreview()
#HotIf

IsWeChatActive() {
    exeList := StrSplit(IniRead(CONFIG_PATH, "wechat", "exe_list", "WeChat.exe,Weixin.exe"), ",")
    activeExe := WinGetProcessName("A")

    for exe in exeList {
        if (Trim(exe) = activeExe) {
            return true
        }
    }

    return false
}

IsPreviewVisible() {
    global PREVIEW_VISIBLE
    return PREVIEW_VISIBLE
}

HandleTabHotkey() {
    global PREVIEW_VISIBLE

    if PREVIEW_VISIBLE {
        SendNextPreviewPart()
        return
    }

    CaptureSelectedText()
}

CaptureSelectedText() {
    oldClipboard := ClipboardAll()
    A_Clipboard := ""
    Send "^c"

    if !ClipWait(ReadFloatConfig("capture", "timeout_seconds", 0.6)) {
        A_Clipboard := oldClipboard
        ShowTip("没有复制到选中文字")
        return
    }

    selectedText := A_Clipboard
    A_Clipboard := oldClipboard

    if (Trim(selectedText) = "") {
        ShowTip("选中文字为空")
        return
    }

    FileDeleteSafe(LAST_SELECTED_PATH)
    FileAppend selectedText, LAST_SELECTED_PATH, "UTF-8"
    ShowTip("已获取：" SubStr(CleanOneLine(selectedText), 1, 36))
    AskRagForSelection()
}

AskRagForSelection() {
    global RAG_QUERY_PID

    if !ReadBoolConfig("rag", "enabled", false) {
        return
    }

    if (RAG_QUERY_PID && ProcessExist(RAG_QUERY_PID)) {
        ShowStatus("loading", "RAG 查询中", "上一次查询还在进行，请稍等...")
        return
    }

    projectRoot := A_ScriptDir "\.."
    python := projectRoot "\.venv\Scripts\python.exe"
    bridge := projectRoot "\customer_rag\wechat_bridge.py"
    logPath := A_ScriptDir "\rag-bridge.log"

    if !FileExist(python) {
        ShowTip("Python venv not found: " python)
        return
    }

    if !FileExist(bridge) {
        ShowTip("RAG bridge not found")
        return
    }

    ShowStatus("loading", "RAG 查询中", "正在根据选中文字生成回复，请稍等...")
    FileDeleteSafe(SEND_TEXT_PATH)
    command := QuoteArg(python) " " QuoteArg(bridge) " --question-file " QuoteArg(LAST_SELECTED_PATH) " --output-file " QuoteArg(SEND_TEXT_PATH) " --project-root " QuoteArg(projectRoot) " --log-file " QuoteArg(logPath)
    try {
        Run command, projectRoot, "Hide", &RAG_QUERY_PID
        SetTimer CheckRagQueryDone, 250
    } catch as exc {
        RAG_QUERY_PID := 0
        ShowStatus("error", "查询启动失败", exc.Message, 4200)
    }
}

CheckRagQueryDone() {
    global RAG_QUERY_PID

    if !RAG_QUERY_PID {
        SetTimer CheckRagQueryDone, 0
        return
    }

    if ProcessExist(RAG_QUERY_PID) {
        return
    }

    RAG_QUERY_PID := 0
    SetTimer CheckRagQueryDone, 0

    if FileExist(SEND_TEXT_PATH) && Trim(ReadSendText()) != "" {
        ShowRagResultPreview()
    } else {
        ShowStatus("error", "查询失败", "请查看 rag-bridge.log 后重试。", 4200)
    }
}

ShowRagResultPreview() {
    text := ReadSendText()
    if (Trim(text) = "") {
        ShowStatus("error", "查询完成但结果为空", "send-text.txt 为空，请查看 rag-bridge.log。", 4200)
        return
    }

    CloseStatus()
    ShowSendPreview(text, "", "", "query")
}

PreviewOrSendNext() {
    global PREVIEW_VISIBLE

    if PREVIEW_VISIBLE {
        SendNextPreviewPart()
        return
    }

    ShowSendPreview("", "", "", "custom")
}

ShowSendPreview(text, mouseX := "", mouseY := "", mode := "query") {
    global PREVIEW_GUI, PREVIEW_VISIBLE, PREVIEW_TARGET_HWND, PREVIEW_SOURCE_TEXT
    global PREVIEW_EDIT, PREVIEW_LIST, PREVIEW_STATUS, PREVIEW_MODE_ALL, PREVIEW_MODE_SPLIT
    global PREVIEW_CLOSE_BUTTON, PREVIEW_TABS, PREVIEW_TAB_TEXTS, PREVIEW_ACTIVE_TAB, PREVIEW_SHOW_TABS

    ClosePreview()
    DebugPreviewTest("show_after_close")

    PREVIEW_TARGET_HWND := WinGetID("A")
    PREVIEW_SOURCE_TEXT := text
    PREVIEW_SHOW_TABS := mode = "custom"
    PREVIEW_TAB_TEXTS := PREVIEW_SHOW_TABS ? BuildCustomTabTexts() : [text]
    PREVIEW_ACTIVE_TAB := 1
    DebugPreviewTest("show_after_target")

    PREVIEW_GUI := Gui("+AlwaysOnTop -Caption -Border +ToolWindow", T("window_title"))
    PREVIEW_GUI.BackColor := "FBF3E8"
    PREVIEW_GUI.MarginX := 15
    PREVIEW_GUI.MarginY := 15

    PREVIEW_GUI.SetFont("s14 bold c2F2620", "Microsoft YaHei")
    PREVIEW_GUI.AddText("xm ym w364 BackgroundFBF3E8", T("preview_title"))
    PREVIEW_GUI.SetFont("s11 bold c7A3F20", "Microsoft YaHei")
    PREVIEW_CLOSE_BUTTON := PREVIEW_GUI.AddText("x+8 yp w28 h28 Center 0x200 +0x100 c7A3F20 BackgroundEAD2B8", "X")
    PREVIEW_CLOSE_BUTTON.OnEvent("Click", CancelPreview)
    DebugPreviewTest("show_after_header")

    PREVIEW_GUI.SetFont("s9 c7A5A43", "Microsoft YaHei")
    PREVIEW_GUI.AddText("xm y+10 w400 BackgroundFBF3E8", T("hint"))

    PREVIEW_GUI.SetFont("s9 bold", "Microsoft YaHei")
    PREVIEW_MODE_ALL := PREVIEW_GUI.AddText("xm y+14 w132 h32 Center 0x200 +0x100", T("send_all"))
    PREVIEW_MODE_SPLIT := PREVIEW_GUI.AddText("x+10 yp w132 h32 Center 0x200 +0x100", T("send_split"))
    PREVIEW_MODE_ALL.OnEvent("Click", (*) => SetPreviewMode("all"))
    PREVIEW_MODE_SPLIT.OnEvent("Click", (*) => SetPreviewMode("split"))

    PREVIEW_TABS := 0
    if PREVIEW_SHOW_TABS {
        PREVIEW_GUI.SetFont("s9 bold", "Microsoft YaHei")
        PREVIEW_TABS := [
            PREVIEW_GUI.AddText("xm y+14 w76 h28 Center 0x200 +0x100", "tab1"),
            PREVIEW_GUI.AddText("x+5 yp w76 h28 Center 0x200 +0x100", "tab2"),
            PREVIEW_GUI.AddText("x+5 yp w76 h28 Center 0x200 +0x100", "tab3"),
            PREVIEW_GUI.AddText("x+5 yp w76 h28 Center 0x200 +0x100", "tab4"),
            PREVIEW_GUI.AddText("x+5 yp w76 h28 Center 0x200 +0x100", "tab5")
        ]
        for index, tabControl in PREVIEW_TABS {
            tabControl.OnEvent("Click", SetPreviewTab.Bind(index))
        }
        RefreshPreviewTabs()
    }

    PREVIEW_GUI.SetFont("s9 c2F2620", "Microsoft YaHei")
    PREVIEW_LIST := PREVIEW_GUI.AddListView("xm y+8 w400 h132 Checked -Multi BackgroundFFF9F2 c2F2620", [T("list_header"), "full_text"])
    PREVIEW_LIST.ModifyCol(1, 376)
    PREVIEW_LIST.ModifyCol(2, 0)

    PREVIEW_GUI.SetFont("s9 bold", "Microsoft YaHei")
    sendButton := PREVIEW_GUI.AddText("xm y+14 w110 h32 Center 0x200 +0x100 cFFFFFF BackgroundD97745", T("send_next"))
    cancelButton := PREVIEW_GUI.AddText("x+10 yp w78 h32 Center 0x200 +0x100 c7A5A43 BackgroundF0DDC8", T("cancel"))
    sendButton.OnEvent("Click", SendPreviewNow)
    cancelButton.OnEvent("Click", CancelPreview)
    PREVIEW_GUI.OnEvent("Close", CancelPreview)
    PREVIEW_GUI.OnEvent("Escape", CancelPreview)
    DebugPreviewTest("show_after_controls")

    PREVIEW_VISIBLE := true
    RebuildPreviewQueue()
    DebugPreviewTest("show_after_rebuild")

    PositionPreviewNearMouse(mouseX, mouseY)
    DebugPreviewTest("show_after_position")
}

SetPreviewMode(mode) {
    IniWrite mode, CONFIG_PATH, "preview", "send_mode"
    RebuildPreviewQueue()
}

RebuildPreviewQueue() {
    global PREVIEW_SOURCE_TEXT, PREVIEW_PARTS, PREVIEW_INDEX, PREVIEW_TAB_TEXTS
    global PREVIEW_ACTIVE_TAB, PREVIEW_TAB_DEFAULT_CHECKED, PREVIEW_SHOW_TABS

    PREVIEW_INDEX := 1
    sourceText := PREVIEW_SOURCE_TEXT
    if IsObject(PREVIEW_TAB_TEXTS) && PREVIEW_ACTIVE_TAB >= 1 && PREVIEW_ACTIVE_TAB <= PREVIEW_TAB_TEXTS.Length {
        sourceText := PREVIEW_TAB_TEXTS[PREVIEW_ACTIVE_TAB]
    }
    PREVIEW_TAB_DEFAULT_CHECKED := !PREVIEW_SHOW_TABS

    if GetPreviewMode() = "split" {
        PREVIEW_PARTS := SplitSendText(sourceText)
    } else {
        PREVIEW_PARTS := [sourceText]
    }

    RefreshModePills()
    UpdatePreviewText()
}

SetPreviewTab(index, *) {
    global PREVIEW_ACTIVE_TAB

    PREVIEW_ACTIVE_TAB := index
    RefreshPreviewTabs()
    RebuildPreviewQueue()
}

RefreshPreviewTabs() {
    global PREVIEW_TABS, PREVIEW_ACTIVE_TAB

    if !IsObject(PREVIEW_TABS) {
        return
    }

    for index, tabControl in PREVIEW_TABS {
        if index = PREVIEW_ACTIVE_TAB {
            tabControl.Opt("cFFFFFF BackgroundD97745")
        } else {
            tabControl.Opt("c7A5A43 BackgroundF0DDC8")
        }
    }
}

RefreshModePills() {
    global PREVIEW_MODE_ALL, PREVIEW_MODE_SPLIT

    if !IsObject(PREVIEW_MODE_ALL) || !IsObject(PREVIEW_MODE_SPLIT) {
        return
    }

    if GetPreviewMode() = "split" {
        PREVIEW_MODE_ALL.Opt("c7A5A43 BackgroundF0DDC8")
        PREVIEW_MODE_SPLIT.Opt("cFFFFFF BackgroundD97745")
    } else {
        PREVIEW_MODE_ALL.Opt("cFFFFFF BackgroundD97745")
        PREVIEW_MODE_SPLIT.Opt("c7A5A43 BackgroundF0DDC8")
    }
}

UpdatePreviewText() {
    global PREVIEW_LIST, PREVIEW_PARTS, PREVIEW_INDEX, PREVIEW_TAB_DEFAULT_CHECKED

    if !IsObject(PREVIEW_LIST) {
        return
    }

    PREVIEW_LIST.Delete()
    for index, part in PREVIEW_PARTS {
        options := (PREVIEW_TAB_DEFAULT_CHECKED && ShouldDefaultCheckPart(part)) ? "Check" : ""
        PREVIEW_LIST.Add(options, MakeListPreview(part), part)
    }
}

ShouldDefaultCheckPart(text) {
    cleaned := CleanOneLine(text)
    if InStr(cleaned, "截团") {
        return false
    }
    return true
}

SendPreviewNow(*) {
    SendNextPreviewPart()
}

SendNextPreviewPart() {
    global PREVIEW_LIST

    if !IsObject(PREVIEW_LIST) {
        ClosePreview()
        return
    }

    row := PREVIEW_LIST.GetNext(0, "Checked")
    if row = 0 {
        ClosePreview()
        return
    }

    text := PREVIEW_LIST.GetText(row, 2)
    PasteTextToWeChat(text)
    PREVIEW_LIST.Delete(row)

    if CountCheckedRows() = 0 {
        ClosePreview()
        return
    }
}

CancelPreview(*) {
    ClosePreview()
}

ClosePreview(*) {
    global PREVIEW_GUI, PREVIEW_VISIBLE, PREVIEW_TARGET_HWND, PREVIEW_SOURCE_TEXT
    global PREVIEW_PARTS, PREVIEW_INDEX, PREVIEW_EDIT, PREVIEW_STATUS, PREVIEW_MODE_ALL, PREVIEW_MODE_SPLIT
    global PREVIEW_LIST, PREVIEW_CLOSE_BUTTON, PREVIEW_TABS, PREVIEW_TAB_TEXTS, PREVIEW_ACTIVE_TAB, PREVIEW_SHOW_TABS

    if IsObject(PREVIEW_GUI) {
        PREVIEW_GUI.Destroy()
    }

    PREVIEW_GUI := 0
    PREVIEW_VISIBLE := false
    PREVIEW_TARGET_HWND := 0
    PREVIEW_SOURCE_TEXT := ""
    PREVIEW_PARTS := []
    PREVIEW_TAB_TEXTS := []
    PREVIEW_ACTIVE_TAB := 1
    PREVIEW_INDEX := 1
    PREVIEW_EDIT := 0
    PREVIEW_LIST := 0
    PREVIEW_STATUS := 0
    PREVIEW_TABS := 0
    PREVIEW_SHOW_TABS := false
    PREVIEW_MODE_ALL := 0
    PREVIEW_MODE_SPLIT := 0
    PREVIEW_CLOSE_BUTTON := 0
}

PasteTextToWeChat(text) {
    global LAST_SEND_TICK, MIN_SEND_INTERVAL_MS

    imagePath := ExtractImagePath(text)
    textToSend := RemoveImageLines(text)
    if (Trim(textToSend) = "" && imagePath = "") {
        ShowTip("消息为空")
        return
    }

    WaitForSendInterval()
    oldClipboard := ClipboardAll()

    FocusSendBox()
    if (imagePath != "" && FileExist(imagePath)) {
        if SetClipboardImage(imagePath) {
            Sleep ReadIntConfig("send", "clipboard_settle_ms", 80)
            Send "^v"
            Sleep ReadIntConfig("send", "after_image_paste_ms", 900)
        } else {
            ShowTip("图片复制失败")
        }
    }

    if (Trim(textToSend) != "") {
        A_Clipboard := textToSend
        Sleep ReadIntConfig("send", "clipboard_settle_ms", 80)
        Send "^v"
        Sleep ReadIntConfig("send", "before_enter_ms", 120)
    }

    if ReadBoolConfig("send", "press_enter", true) {
        Send "{Enter}"
    }

    A_Clipboard := oldClipboard
    LAST_SEND_TICK := A_TickCount
    ShowTip("Sent")
}

ExtractImagePath(text) {
    normalized := StrReplace(text, "`r`n", "`n")
    normalized := StrReplace(normalized, "`r", "`n")
    for line in StrSplit(normalized, "`n") {
        if RegExMatch(line, "i)^\s*(?:[-•]\s*)?图片\s*[：:]\s*(.+?)\s*$", &match) {
            path := Trim(match[1], " `t`"")
            return ResolveProjectPath(path)
        }
    }
    return ""
}

RemoveImageLines(text) {
    normalized := StrReplace(text, "`r`n", "`n")
    normalized := StrReplace(normalized, "`r", "`n")
    output := ""
    for line in StrSplit(normalized, "`n") {
        if RegExMatch(line, "i)^\s*(?:[-•]\s*)?图片\s*[：:].*$") {
            continue
        }
        output .= (output = "" ? "" : "`r`n") line
    }
    return Trim(output, "`r`n `t")
}

ResolveProjectPath(path) {
    if (path = "") {
        return ""
    }
    if FileExist(path) {
        return path
    }
    projectRoot := A_ScriptDir "\.."
    candidate := projectRoot "\" path
    if FileExist(candidate) {
        return candidate
    }
    return path
}

SetClipboardFile(path) {
    absolutePath := path
    bytes := StrPut(absolutePath, "UTF-16") * 2 + 2
    size := 20 + bytes
    hDrop := DllCall("GlobalAlloc", "UInt", 0x42, "UPtr", size, "UPtr")
    if !hDrop {
        return false
    }
    ptr := DllCall("GlobalLock", "UPtr", hDrop, "UPtr")
    if !ptr {
        DllCall("GlobalFree", "UPtr", hDrop)
        return false
    }
    NumPut("UInt", 20, ptr, 0)
    NumPut("Int", 0, ptr, 4)
    NumPut("Int", 0, ptr, 8)
    NumPut("Int", 0, ptr, 12)
    NumPut("Int", 1, ptr, 16)
    StrPut(absolutePath, ptr + 20, "UTF-16")
    NumPut("UShort", 0, ptr, 20 + StrPut(absolutePath, "UTF-16") * 2)
    DllCall("GlobalUnlock", "UPtr", hDrop)
    if !DllCall("OpenClipboard", "UPtr", 0) {
        DllCall("GlobalFree", "UPtr", hDrop)
        return false
    }
    DllCall("EmptyClipboard")
    if !DllCall("SetClipboardData", "UInt", 15, "UPtr", hDrop, "UPtr") {
        DllCall("CloseClipboard")
        DllCall("GlobalFree", "UPtr", hDrop)
        return false
    }
    DllCall("CloseClipboard")
    return true
}

SetClipboardImage(path) {
    imagePath := ResolveProjectPath(path)
    if !FileExist(imagePath) {
        return false
    }
    scriptPath := A_Temp "\wechat-set-image-clipboard.ps1"
    escapedPath := StrReplace(imagePath, "'", "''")
    script := "$ErrorActionPreference = 'Stop'`r`n"
        . "Add-Type -AssemblyName System.Windows.Forms`r`n"
        . "Add-Type -AssemblyName System.Drawing`r`n"
        . "$imagePath = '" escapedPath "'`r`n"
        . "$img = [System.Drawing.Image]::FromFile($imagePath)`r`n"
        . "try {`r`n"
        . "    [System.Windows.Forms.Clipboard]::Clear()`r`n"
        . "    [System.Windows.Forms.Clipboard]::SetImage($img)`r`n"
        . "} finally {`r`n"
        . "    $img.Dispose()`r`n"
        . "}`r`n"
    FileDeleteSafe(scriptPath)
    FileAppend script, scriptPath, "UTF-8"
    command := "powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File " QuoteArg(scriptPath)
    try {
        exitCode := RunWait(command, , "Hide")
        return exitCode = 0
    } catch {
        return false
    }
}

WaitForSendInterval() {
    global LAST_SEND_TICK, MIN_SEND_INTERVAL_MS

    elapsed := A_TickCount - LAST_SEND_TICK
    if (LAST_SEND_TICK > 0 && elapsed < MIN_SEND_INTERVAL_MS) {
        Sleep MIN_SEND_INTERVAL_MS - elapsed
    }
}

FocusSendBox() {
    global PREVIEW_TARGET_HWND

    targetHwnd := PREVIEW_TARGET_HWND ? PREVIEW_TARGET_HWND : WinGetID("A")
    WinActivate "ahk_id " targetHwnd
    Sleep 80

    if !ReadBoolConfig("send", "click_before_paste", true) {
        return
    }

    GetInputAnchor(targetHwnd, &anchorX, &anchorY)
    Click anchorX, anchorY
    Sleep ReadIntConfig("send", "after_click_ms", 100)
}

PositionPreviewNearMouse(mouseX := "", mouseY := "") {
    global PREVIEW_GUI, PREVIEW_EDIT, PREVIEW_CLOSE_BUTTON

    if !IsObject(PREVIEW_GUI) {
        return
    }

    if (mouseX = "" || mouseY = "") {
        MouseGetPos &mouseX, &mouseY
    }

    GetWorkAreaForPoint(mouseX, mouseY, &left, &top, &right, &bottom)
    gap := 14
    firstX := Clamp(mouseX + gap, left + 10, right - 520)
    firstY := Clamp(mouseY + gap, top + 10, bottom - 360)

    PREVIEW_GUI.Show("x" firstX " y" firstY " AutoSize")
    Sleep 30

    DebugPreviewTest("position_start")
    try {
        WinGetPos ,, &previewW, &previewH, "ahk_id " PREVIEW_GUI.Hwnd
    } catch {
        ApplyRoundedPreviewRegion()
        DebugPreviewTest("position_wingetpos_failed")
        return
    }
    DebugPreviewTest("position_after_wingetpos")

    candidates := [
        {x: mouseX + gap, y: mouseY + gap},
        {x: mouseX - previewW - gap, y: mouseY + gap},
        {x: mouseX + gap, y: mouseY - previewH - gap},
        {x: mouseX - previewW - gap, y: mouseY - previewH - gap}
    ]

    best := ""
    for candidate in candidates {
        if FitsInWorkArea(candidate.x, candidate.y, previewW, previewH, left, top, right, bottom) {
            best := candidate
            break
        }
    }

    if !IsObject(best) {
        best := {
            x: Clamp(mouseX + gap, left + 10, right - previewW - 10),
            y: Clamp(mouseY + gap, top + 10, bottom - previewH - 10)
        }
    }

    PREVIEW_GUI.Show("x" best.x " y" best.y " AutoSize")
    Sleep 30
    DebugPreviewTest("position_after_show")
    ClearPreviewSelection()
    ApplyRoundedPreviewRegion()
    DebugPreviewTest("position_after_region")
}

ClearPreviewSelection() {
    global PREVIEW_GUI, PREVIEW_EDIT, PREVIEW_LIST

    if IsObject(PREVIEW_GUI) {
        try DllCall("SetFocus", "ptr", PREVIEW_GUI.Hwnd)
    }
}

FitsInWorkArea(x, y, w, h, left, top, right, bottom) {
    return x >= left + 10 && y >= top + 10 && x + w <= right - 10 && y + h <= bottom - 10
}

GetInputAnchor(hwnd, &anchorX, &anchorY) {
    WinGetPos &winX, &winY, &winW, &winH, "ahk_id " hwnd
    inputX := ReadIntConfig("send", "input_x", 520)
    inputY := ReadIntConfig("send", "input_y", 690)

    if IsBadInputPoint(inputX, inputY, winW, winH) {
        anchorX := winX + Floor(winW * 0.72)
        anchorY := winY + winH - 86
        return
    }

    anchorX := winX + inputX
    anchorY := winY + inputY
}

IsBadInputPoint(inputX, inputY, winW, winH) {
    return inputX < 0 || inputY < 0 || inputX > winW || inputY > winH
}

GetWorkAreaForPoint(x, y, &left, &top, &right, &bottom) {
    count := MonitorGetCount()
    Loop count {
        MonitorGetWorkArea A_Index, &monLeft, &monTop, &monRight, &monBottom
        if (x >= monLeft && x <= monRight && y >= monTop && y <= monBottom) {
            left := monLeft
            top := monTop
            right := monRight
            bottom := monBottom
            return
        }
    }

    MonitorGetWorkArea 1, &left, &top, &right, &bottom
}

Clamp(value, minValue, maxValue) {
    if value < minValue {
        return minValue
    }
    if value > maxValue {
        return maxValue
    }
    return value
}

ApplyRoundedPreviewRegion() {
    global PREVIEW_GUI

    if !IsObject(PREVIEW_GUI) {
        return
    }

    WinGetPos ,, &width, &height, "ahk_id " PREVIEW_GUI.Hwnd
    try {
        WinSetRegion "0-0 w" width " h" height " r20-20", "ahk_id " PREVIEW_GUI.Hwnd
    }
}

CalibrateSendBoxPoint() {
    MouseGetPos &mouseX, &mouseY
    WinGetPos &winX, &winY,,, "A"
    inputX := mouseX - winX
    inputY := mouseY - winY

    IniWrite inputX, CONFIG_PATH, "send", "input_x"
    IniWrite inputY, CONFIG_PATH, "send", "input_y"
    ShowTip("Saved input point: " inputX ", " inputY)
}

ReadSendText() {
    if FileExist(SEND_TEXT_PATH) {
        return FileRead(SEND_TEXT_PATH, "UTF-8")
    }

    return IniRead(CONFIG_PATH, "send", "text", "")
}

BuildCustomTabTexts() {
    global CUSTOM_TAB_PATHS

    texts := []
    for path in CUSTOM_TAB_PATHS {
        if FileExist(path) {
            texts.Push(StripBom(FileRead(path, "UTF-8")))
        } else {
            texts.Push("")
        }
    }
    return texts
}

SplitSendText(text) {
    parts := []
    current := ""
    text := StripBom(text)
    if (Trim(text, "`r`n `t") = "") {
        return parts
    }
    normalized := StrReplace(text, "`r`n", "`n")
    normalized := StrReplace(normalized, "`r", "`n")

    for line in StrSplit(normalized, "`n") {
        if IsSeparatorLine(line) {
            AddPart(parts, current)
            current := ""
        } else {
            current .= (current = "" ? "" : "`n") line
        }
    }

    AddPart(parts, current)

    if parts.Length = 0 && Trim(text, "`r`n `t") != "" {
        parts.Push(text)
    }

    return parts
}

StripBom(text) {
    return StrReplace(text, Chr(0xFEFF), "")
}

IsSeparatorLine(line) {
    trimmed := Trim(line)
    return RegExMatch(trimmed, "^-{3,}$")
}

AddPart(parts, text) {
    trimmed := Trim(text, "`r`n `t")
    if (trimmed != "") {
        parts.Push(trimmed)
    }
}

CountCheckedRows() {
    global PREVIEW_LIST

    if !IsObject(PREVIEW_LIST) {
        return 0
    }

    count := 0
    row := 0
    loop {
        row := PREVIEW_LIST.GetNext(row, "Checked")
        if row = 0 {
            break
        }
        count += 1
    }

    return count
}

MakeListPreview(text) {
    preview := CleanOneLine(text)
    if StrLen(preview) > 42 {
        preview := SubStr(preview, 1, 42) "..."
    }
    return preview
}

JoinParts(parts) {
    output := ""
    for index, part in parts {
        output .= (index = 1 ? "" : "`r`n---`r`n") part
    }
    return output
}

GetPreviewMode() {
    mode := StrLower(IniRead(CONFIG_PATH, "preview", "send_mode", "all"))
    return mode = "split" ? "split" : "all"
}

T(key) {
    switch key {
        case "window_title":
            return C(24494, 20449, 21457, 36865, 39044, 35272)
        case "preview_title":
            return C(21457, 36865, 39044, 35272)
        case "hint":
            return "~ " C(38190) " " C(21457, 36865, 19979, 19968, 26465) ", Esc " C(21462, 28040) "." C(20998, 21106, 32447) ": " C(21333, 29420, 19968, 34892) " ---"
        case "send_all":
            return C(19968, 27425, 24615, 21457, 36865)
        case "send_split":
            return C(20998, 26465, 21457, 36865)
        case "send_next":
            return C(21457, 36865, 19979, 19968, 26465)
        case "cancel":
            return C(21462, 28040)
        case "list_header":
            return C(21246, 36873, 21457, 36865, 26465, 30446)
        case "checked_status":
            return C(24050, 21246, 36873) " "
        case "split_status":
            return C(20998, 26465, 21457, 36865) ": " C(19979, 19968, 26465) " "
        case "all_status":
            return C(19968, 27425, 24615, 21457, 36865) ": " C(20840, 37096, 20869, 23481, 23558, 20316, 20026, 19968, 26465, 28040, 24687, 21457, 36865)
    }

    return key
}

C(chars*) {
    output := ""
    for code in chars {
        output .= Chr(code)
    }
    return output
}

EnsureBootstrapFiles() {
    global CUSTOM_TAB_PATHS

    if !FileExist(CONFIG_PATH) {
        FileAppend DefaultConfig(), CONFIG_PATH, "UTF-8"
    }

    if !FileExist(SEND_TEXT_PATH) {
        FileAppend "Edit this file with the message to send.", SEND_TEXT_PATH, "UTF-8"
    }

    for path in CUSTOM_TAB_PATHS {
        if !FileExist(path) {
            FileAppend "", path, "UTF-8"
        }
    }
}

DefaultConfig() {
    return "
(
[wechat]
exe_list=WeChat.exe,Weixin.exe

[capture]
timeout_seconds=0.6

[preview]
send_mode=all

[send]
text=
click_before_paste=1
input_x=520
input_y=690
after_click_ms=100
clipboard_settle_ms=80
after_image_paste_ms=900
before_enter_ms=120
press_enter=1

[rag]
enabled=1
)"
}

HasArg(expected) {
    for arg in A_Args {
        if arg = expected {
            return true
        }
    }
    return false
}

DebugPreviewTest(message) {
    if HasArg("--preview-test") {
        FileAppend message "=1`n", PREVIEW_TEST_PATH, "UTF-8"
    }
}

RunPreviewSelfTest() {
    FileDeleteSafe(PREVIEW_TEST_PATH)
    FileAppend "[debug]`nstarted=1`n", PREVIEW_TEST_PATH, "UTF-8"
    sample := "杩欓噷濉啓鎸夌┖鏍煎悗瑕佸彂閫佺殑鍐呭1`n---`n杩欓噷濉啓鎸夌┖鏍煎悗瑕佸彂閫佺殑鍐呭2`n---`n杩欓噷濉啓鎸夌┖鏍煎悗瑕佸彂閫佺殑鍐呭3"
    mouseX := 360
    mouseY := 240
    FileAppend "before_show=1`n", PREVIEW_TEST_PATH, "UTF-8"
    ShowSendPreview(sample, mouseX, mouseY)
    FileAppend "after_show=1`n", PREVIEW_TEST_PATH, "UTF-8"
    Sleep 250

    WinGetPos &x, &y, &w, &h, "ahk_id " PREVIEW_GUI.Hwnd
    okSize := w >= 560 && w <= 760 && h >= 430 && h <= 620
    okNearMouse := Abs(x - mouseX) <= 40 || Abs((x + w) - mouseX) <= 40
    FileAppend "[result]`n", PREVIEW_TEST_PATH, "UTF-8"
    FileAppend "x=" x "`ny=" y "`nw=" w "`nh=" h "`n", PREVIEW_TEST_PATH, "UTF-8"
    FileAppend "mouse_x=" mouseX "`nmouse_y=" mouseY "`n", PREVIEW_TEST_PATH, "UTF-8"
    FileAppend "ok_size=" (okSize ? "1" : "0") "`n", PREVIEW_TEST_PATH, "UTF-8"
    FileAppend "ok_near_mouse=" (okNearMouse ? "1" : "0") "`n", PREVIEW_TEST_PATH, "UTF-8"
    Sleep 350
    ClosePreview()
}

RunStatusSelfTest() {
    global STATUS_TEST_FORCE_MONITOR

    resultPath := A_ScriptDir "\status-test-result.ini"
    FileDeleteSafe(resultPath)
    FileAppend "[debug]`nstarted=1`n", resultPath, "UTF-8"
    count := MonitorGetCount()
    FileAppend "monitor_count=" count "`n", resultPath, "UTF-8"
    Loop count {
        MonitorGetWorkArea A_Index, &ml, &mt, &mr, &mb
        FileAppend "monitor" A_Index "=" ml "," mt "," mr "," mb "`n", resultPath, "UTF-8"
    }
    STATUS_TEST_FORCE_MONITOR := 2
    ShowStatus("loading", "RAG 查询中", "正在根据选中文字生成回复，请稍等...")
    if IsObject(STATUS_GUI) {
        try {
            WinGetPos &x, &y, &w, &h, "ahk_id " STATUS_GUI.Hwnd
            FileAppend "hwnd=" STATUS_GUI.Hwnd "`nx=" x "`ny=" y "`nw=" w "`nh=" h "`n", resultPath, "UTF-8"
            FileAppend "exists=" (WinExist("ahk_id " STATUS_GUI.Hwnd) ? "1" : "0") "`n", resultPath, "UTF-8"
        } catch as exc {
            FileAppend "pos_error=" exc.Message "`n", resultPath, "UTF-8"
        }
    } else {
        FileAppend "status_gui=0`n", resultPath, "UTF-8"
    }
    Sleep 5000
    CloseStatus()
    STATUS_TEST_FORCE_MONITOR := 0
}

RunClipboardFileSelfTest() {
    resultPath := A_ScriptDir "\clipboard-file-test-result.ini"
    imagePath := A_ScriptDir "\..\data\tmp\order_flow_preview\test\row-2-330a5ea49e8d8164.png"
    FileDeleteSafe(resultPath)
    FileAppend "[debug]`nstarted=1`nimage=" imagePath "`nexists=" (FileExist(imagePath) ? "1" : "0") "`n", resultPath, "UTF-8"
    if FileExist(imagePath) {
        ok := SetClipboardFile(imagePath)
        FileAppend "set_clipboard_file=" (ok ? "1" : "0") "`n", resultPath, "UTF-8"
    }
}

RunClipboardImageSelfTest() {
    resultPath := A_ScriptDir "\clipboard-image-test-result.ini"
    imagePath := A_ScriptDir "\..\data\tmp\order_flow_preview\test\row-2-330a5ea49e8d8164.png"
    FileDeleteSafe(resultPath)
    FileAppend "[debug]`nstarted=1`nimage=" imagePath "`nexists=" (FileExist(imagePath) ? "1" : "0") "`n", resultPath, "UTF-8"
    if FileExist(imagePath) {
        ok := SetClipboardImage(imagePath)
        FileAppend "set_clipboard_image=" (ok ? "1" : "0") "`n", resultPath, "UTF-8"
    }
}

ReadBoolConfig(section, key, defaultValue) {
    value := IniRead(CONFIG_PATH, section, key, defaultValue ? "1" : "0")
    return value = "1" || StrLower(value) = "true" || StrLower(value) = "yes"
}

ReadIntConfig(section, key, defaultValue) {
    value := IniRead(CONFIG_PATH, section, key, defaultValue)
    return Integer(value)
}

ReadFloatConfig(section, key, defaultValue) {
    value := IniRead(CONFIG_PATH, section, key, defaultValue)
    return Number(value)
}

FileDeleteSafe(path) {
    if FileExist(path) {
        FileDelete path
    }
}

CleanOneLine(text) {
    text := StrReplace(text, "`r", " ")
    text := StrReplace(text, "`n", " ")
    text := RegExReplace(text, "\s+", " ")
    return Trim(text)
}

QuoteArg(value) {
    quote := Chr(34)
    return quote StrReplace(value, quote, quote quote) quote
}

ShowStatus(kind, title, body, autoCloseMs := 0) {
    global STATUS_GUI, STATUS_TITLE, STATUS_BODY

    ToolTip()
    CloseStatus()

    colors := StatusColors(kind)
    STATUS_GUI := Gui("+AlwaysOnTop -Caption -Border +ToolWindow", "RAG Status")
    STATUS_GUI.BackColor := colors.bg
    STATUS_GUI.MarginX := 16
    STATUS_GUI.MarginY := 13

    STATUS_GUI.SetFont("s10 bold c" colors.title, "Microsoft YaHei")
    STATUS_TITLE := STATUS_GUI.AddText("xm ym w300 Background" colors.bg, StatusIcon(kind) " " title)
    STATUS_GUI.SetFont("s9 c" colors.body, "Microsoft YaHei")
    STATUS_BODY := STATUS_GUI.AddText("xm y+7 w300 Background" colors.bg, body)

    PositionStatus()
    ApplyStatusRegion()

    if autoCloseMs > 0 {
        SetTimer CloseStatus, -autoCloseMs
    }
}

CloseStatus(*) {
    global STATUS_GUI, STATUS_TITLE, STATUS_BODY

    if IsObject(STATUS_GUI) {
        STATUS_GUI.Destroy()
    }

    STATUS_GUI := 0
    STATUS_TITLE := 0
    STATUS_BODY := 0
}

PositionStatus() {
    global STATUS_GUI, STATUS_TEST_FORCE_MONITOR

    if !IsObject(STATUS_GUI) {
        return
    }

    MouseGetPos &mouseX, &mouseY
    if STATUS_TEST_FORCE_MONITOR {
        MonitorGetWorkArea STATUS_TEST_FORCE_MONITOR, &left, &top, &right, &bottom
        mouseX := left + Floor((right - left) / 2)
        mouseY := top + Floor((bottom - top) / 2)
    } else {
        GetWorkAreaForPoint(mouseX, mouseY, &left, &top, &right, &bottom)
    }
    width := 340
    height := 92
    x := mouseX + 18
    y := mouseY + 24
    if (x + width > right) {
        x := right - width - 20
    }
    if (y + height > bottom) {
        y := mouseY - height - 20
    }
    if (x < left + 12) {
        x := left + 12
    }
    if (y < top + 12) {
        y := top + 12
    }
    try {
        STATUS_GUI.Show("x" x " y" y " w" width " h" height)
        DllCall(
            "SetWindowPos",
            "ptr", STATUS_GUI.Hwnd,
            "ptr", -1,
            "int", x,
            "int", y,
            "int", width,
            "int", height,
            "uint", 0x0040
        )
        DllCall("ShowWindow", "ptr", STATUS_GUI.Hwnd, "int", 5)
        DllCall("RedrawWindow", "ptr", STATUS_GUI.Hwnd, "ptr", 0, "ptr", 0, "uint", 0x0101)
    } catch {
        CloseStatus()
    }
}

ApplyStatusRegion() {
    global STATUS_GUI

    if !IsObject(STATUS_GUI) {
        return
    }

    try {
        hwnd := STATUS_GUI.Hwnd
        if !hwnd || !WinExist("ahk_id " hwnd) {
            return
        }
        WinGetPos ,, &width, &height, "ahk_id " hwnd
        WinSetRegion "0-0 w" width " h" height " r16-16", "ahk_id " hwnd
    }
}

StatusColors(kind) {
    switch kind {
        case "success":
            return {bg: "EAF7EF", title: "19663A", body: "2F5A43"}
        case "error":
            return {bg: "FDECEC", title: "B42318", body: "7A271A"}
        default:
            return {bg: "FFF6E5", title: "9A4A12", body: "6E4B21"}
    }
}

StatusIcon(kind) {
    switch kind {
        case "success":
            return "[OK]"
        case "error":
            return "[!]"
        default:
            return "[...]"
    }
}

ShowTip(text) {
    ToolTip text
    SetTimer () => ToolTip(), -1200
}

