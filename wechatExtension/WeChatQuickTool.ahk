#Requires AutoHotkey v2.0
#SingleInstance Force
#NoTrayIcon

try DllCall("SetThreadDpiAwarenessContext", "ptr", -4, "ptr")
SetTitleMatchMode 2
CoordMode "Mouse", "Screen"
CoordMode "ToolTip", "Screen"

global CONFIG_PATH := A_ScriptDir "\config.ini"
global SEND_TEXT_PATH := A_ScriptDir "\send-text.txt"
global LAST_SELECTED_PATH := A_ScriptDir "\last-selected.txt"
global RAG_BRANDS_PATH := A_ScriptDir "\rag-brands.txt"
global PREVIEW_TEST_PATH := A_ScriptDir "\preview-test-result.ini"
global SEND_BOX_DEBUG_PATH := A_ScriptDir "\sendbox-debug.log"
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
global PREVIEW_FALLBACK_MODE := false
global PREVIEW_MODE_ALL := 0
global PREVIEW_MODE_SPLIT := 0
global PREVIEW_BRAND_SELECT := 0
global PREVIEW_RETURN_COUNT_SELECT := 0
global PREVIEW_BRANDS := []
global PREVIEW_SELECTED_BRAND := ""
global PREVIEW_FORCE_SPLIT := false
global PREVIEW_CLOSE_BUTTON := 0
global LAST_SEND_TICK := 0
global MIN_SEND_INTERVAL_MS := 300
global SEND_IN_PROGRESS := false
global STATUS_GUI := 0
global STATUS_TITLE := 0
global STATUS_BODY := 0
global RAG_QUERY_PID := 0
global RAG_QUERY_STARTED_AT := 0
global RAG_TALK_ONLY_PENDING := false
global RAG_ORIGINAL_QUESTION := ""
global RAG_SELECTED_BRAND := ""
global RAG_PREVIEW_X := ""
global RAG_PREVIEW_Y := ""
global STATUS_TEST_FORCE_MONITOR := 0
global QUERY_GUI := 0
global QUERY_VISIBLE := false
global QUERY_EDIT := 0
global PASTE_ONLY_MODE := false

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

if HasArg("--paste-image-test") {
    RunPasteImageSelfTest()
    ExitApp
}

if HasArg("--send-box-test") {
    RunSendBoxSelfTest()
    ExitApp
}

#HotIf IsWeChatActive()
Tab::HandleTabHotkey()
vkC0::PreviewOrSendNext()
F8::CalibrateSendBoxPoint()
#HotIf

#HotIf IsQueryVisible()
Tab::StartRagFromQueryDialog()
Esc::CloseQueryDialog()
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

IsQueryVisible() {
    global QUERY_VISIBLE
    return QUERY_VISIBLE
}

HandleTabHotkey() {
    global PREVIEW_VISIBLE, SEND_IN_PROGRESS

    if SEND_IN_PROGRESS {
        return
    }

    if PREVIEW_VISIBLE {
        SendNextPreviewPart()
        return
    }

    CaptureSelectedText()
}

CaptureSelectedText() {
    global RAG_ORIGINAL_QUESTION, RAG_SELECTED_BRAND, RAG_PREVIEW_X, RAG_PREVIEW_Y

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
    RAG_ORIGINAL_QUESTION := selectedText
    RAG_SELECTED_BRAND := ""
    RAG_PREVIEW_X := ""
    RAG_PREVIEW_Y := ""
    ShowQueryDialog(selectedText)
}

ShowQueryDialog(selectedText) {
    global QUERY_GUI, QUERY_VISIBLE, QUERY_EDIT

    CloseQueryDialog()

    QUERY_GUI := Gui("+AlwaysOnTop -Caption -Border +ToolWindow", "RAG 查询")
    QUERY_GUI.BackColor := "FBF3E8"
    QUERY_GUI.MarginX := 8
    QUERY_GUI.MarginY := 8
    QUERY_GUI.SetFont("s10 c2F2620", "Microsoft YaHei")

    QUERY_EDIT := QUERY_GUI.AddEdit("xm ym w260 h28", selectedText)
    closeButton := QUERY_GUI.AddText("x+8 yp w28 h28 Center 0x200 +0x100 c7A3F20 BackgroundEAD2B8", "X")
    closeButton.OnEvent("Click", CloseQueryDialog)
    QUERY_GUI.OnEvent("Close", CloseQueryDialog)
    QUERY_GUI.OnEvent("Escape", CloseQueryDialog)

    QUERY_VISIBLE := true
    PositionQueryDialog()
    try DllCall("SetFocus", "ptr", QUERY_EDIT.Hwnd)
}

StartRagFromQueryDialog(*) {
    global QUERY_EDIT, RAG_QUERY_PID, SEND_IN_PROGRESS, RAG_ORIGINAL_QUESTION, RAG_SELECTED_BRAND, RAG_PREVIEW_X, RAG_PREVIEW_Y

    if SEND_IN_PROGRESS {
        return
    }

    if (RAG_QUERY_PID && ProcessExist(RAG_QUERY_PID)) {
        ShowStatus("loading", "RAG 查询中", "当前查询还在进行，请稍等...")
        return
    }
    if !IsObject(QUERY_EDIT) {
        return
    }

    question := Trim(QUERY_EDIT.Value)
    if (question = "") {
        ShowTip("查询内容为空")
        return
    }

    RAG_ORIGINAL_QUESTION := question
    RAG_SELECTED_BRAND := ""
    RAG_PREVIEW_X := ""
    RAG_PREVIEW_Y := ""
    FileDeleteSafe(LAST_SELECTED_PATH)
    FileAppend question, LAST_SELECTED_PATH, "UTF-8"
    CloseQueryDialog()
    AskRagForSelection()
}

PositionQueryDialog() {
    global QUERY_GUI

    if !IsObject(QUERY_GUI) {
        return
    }
    MouseGetPos &mouseX, &mouseY
    GetWorkAreaForPoint(mouseX, mouseY, &left, &top, &right, &bottom)
    x := Clamp(mouseX + 14, left + 10, right - 340)
    y := Clamp(mouseY + 14, top + 10, bottom - 80)
    QUERY_GUI.Show("x" x " y" y " AutoSize")
}

CloseQueryDialog(*) {
    global QUERY_GUI, QUERY_VISIBLE, QUERY_EDIT

    if IsObject(QUERY_GUI) {
        QUERY_GUI.Destroy()
    }
    QUERY_GUI := 0
    QUERY_VISIBLE := false
    QUERY_EDIT := 0
}

AskRagForSelection(tags := "", talkOnly := false) {
    global RAG_QUERY_PID, RAG_QUERY_STARTED_AT, RAG_TALK_ONLY_PENDING, RAG_SELECTED_BRAND

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
    RAG_TALK_ONLY_PENDING := talkOnly
    FileDeleteSafe(SEND_TEXT_PATH)
    if (Trim(RAG_SELECTED_BRAND) = "") {
        FileDeleteSafe(RAG_BRANDS_PATH)
    }
    command := QuoteArg(python) " " QuoteArg(bridge) " --question-file " QuoteArg(LAST_SELECTED_PATH) " --output-file " QuoteArg(SEND_TEXT_PATH) " --project-root " QuoteArg(projectRoot) " --log-file " QuoteArg(logPath)
    if (Trim(RAG_SELECTED_BRAND) != "") {
        command .= " --brand " QuoteArg(RAG_SELECTED_BRAND)
    }
    if (!talkOnly) {
        command .= " --brands-file " QuoteArg(RAG_BRANDS_PATH)
        command .= " --top-k " ReadPreviewReturnCount()
    }
    if (Trim(tags) != "") {
        command .= " --tags " QuoteArg(tags)
    }
    if (talkOnly) {
        command .= " --talk-only"
    }
    try {
        Run command, projectRoot, "Hide", &RAG_QUERY_PID
        RAG_QUERY_STARTED_AT := A_TickCount
        SetTimer CheckRagQueryDone, 250
    } catch as exc {
        RAG_QUERY_PID := 0
        RAG_QUERY_STARTED_AT := 0
        RAG_TALK_ONLY_PENDING := false
        ShowStatus("error", "查询启动失败", exc.Message, 4200)
    }
}

CheckRagQueryDone() {
    global RAG_QUERY_PID, RAG_QUERY_STARTED_AT, RAG_TALK_ONLY_PENDING

    if !RAG_QUERY_PID {
        SetTimer CheckRagQueryDone, 0
        RAG_QUERY_STARTED_AT := 0
        return
    }

    if ProcessExist(RAG_QUERY_PID) {
        configuredSeconds := ReadFloatConfig("rag", "fallback_seconds", 5.0)
        fallbackSeconds := RAG_TALK_ONLY_PENDING ? Max(1, configuredSeconds) : Max(5, configuredSeconds)
        fallbackMs := fallbackSeconds * 1000
        if (RAG_QUERY_STARTED_AT && A_TickCount - RAG_QUERY_STARTED_AT >= fallbackMs) {
            try ProcessClose RAG_QUERY_PID
            RAG_QUERY_PID := 0
            RAG_QUERY_STARTED_AT := 0
            SetTimer CheckRagQueryDone, 0
            if (RAG_TALK_ONLY_PENDING) {
                RAG_TALK_ONLY_PENDING := false
                ShowStatus("error", "话术查询超时", "超过 " Round(fallbackMs / 1000, 1) " 秒未返回，请稍后重试。", 4200)
            } else {
                WriteRagFallbackResult()
                RAG_TALK_ONLY_PENDING := false
                ShowRagResultPreview()
            }
        }
        return
    }

    RAG_QUERY_PID := 0
    RAG_QUERY_STARTED_AT := 0
    SetTimer CheckRagQueryDone, 0

    if FileExist(SEND_TEXT_PATH) && Trim(ReadSendText()) != "" {
        if (RAG_TALK_ONLY_PENDING) {
            text := ReadSendText()
            RAG_TALK_ONLY_PENDING := false
            CloseStatus()
            PasteTextToWeChat(text)
        } else {
            ShowRagResultPreview()
        }
    } else {
        RAG_TALK_ONLY_PENDING := false
        ShowStatus("error", "查询失败", "请查看 rag-bridge.log 后重试。", 4200)
    }
}

WriteRagFallbackResult() {
    FileDeleteSafe(SEND_TEXT_PATH)
    FileAppend "__RAG_FUZZY_FALLBACK__`n资料中未找到相关信息", SEND_TEXT_PATH, "UTF-8"
}

ShowRagResultPreview() {
    global RAG_PREVIEW_X, RAG_PREVIEW_Y

    text := ReadSendText()
    if (Trim(text) = "") {
        ShowStatus("error", "查询完成但结果为空", "send-text.txt 为空，请查看 rag-bridge.log。", 4200)
        return
    }

    CloseStatus()
    if (RAG_PREVIEW_X != "" && RAG_PREVIEW_Y != "") {
        ShowSendPreview(text, RAG_PREVIEW_X, RAG_PREVIEW_Y, "query_fixed")
    } else {
        ShowSendPreview(text, "", "", "query")
    }
}

PreviewOrSendNext() {
    global PREVIEW_VISIBLE

    if PREVIEW_VISIBLE {
        SendNextPreviewPart()
        return
    }

    CaptureSelectedTextForTalk()
}

CaptureSelectedTextForTalk() {
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
    AskRagForSelection("", true)
}

ShowSendPreview(text, mouseX := "", mouseY := "", mode := "query") {
    global PREVIEW_GUI, PREVIEW_VISIBLE, PREVIEW_TARGET_HWND, PREVIEW_SOURCE_TEXT
    global PREVIEW_EDIT, PREVIEW_LIST, PREVIEW_STATUS, PREVIEW_MODE_ALL, PREVIEW_MODE_SPLIT
    global PREVIEW_CLOSE_BUTTON, PREVIEW_TABS, PREVIEW_TAB_TEXTS, PREVIEW_ACTIVE_TAB, PREVIEW_SHOW_TABS
    global PREVIEW_FALLBACK_MODE, PREVIEW_BRAND_SELECT, PREVIEW_RETURN_COUNT_SELECT
    global PREVIEW_BRANDS, PREVIEW_SELECTED_BRAND, PREVIEW_FORCE_SPLIT
    global RAG_SELECTED_BRAND

    ClosePreview()
    DebugPreviewTest("show_after_close")

    text := PreparePreviewSourceText(text)
    PREVIEW_TARGET_HWND := WinGetID("A")
    PREVIEW_SOURCE_TEXT := text
    PREVIEW_SHOW_TABS := mode = "custom"
    PREVIEW_FORCE_SPLIT := InStr(mode, "query") = 1
    PREVIEW_TAB_TEXTS := PREVIEW_SHOW_TABS ? BuildCustomTabTexts() : [text]
    PREVIEW_ACTIVE_TAB := 1
    PREVIEW_BRANDS := PREVIEW_SHOW_TABS ? [] : ReadQueryBrands()
    PREVIEW_SELECTED_BRAND := RAG_SELECTED_BRAND
    DebugPreviewTest("show_after_target")

    PREVIEW_GUI := Gui("+AlwaysOnTop -Caption -Border +ToolWindow", T("window_title"))
    PREVIEW_GUI.BackColor := "FBF3E8"
    PREVIEW_GUI.MarginX := 15
    PREVIEW_GUI.MarginY := 15

    PREVIEW_GUI.SetFont("s14 bold c2F2620", "Microsoft YaHei")
    PREVIEW_GUI.AddText("xm ym w440 BackgroundFBF3E8", T("preview_title"))
    PREVIEW_GUI.SetFont("s11 bold c7A3F20", "Microsoft YaHei")
    PREVIEW_CLOSE_BUTTON := PREVIEW_GUI.AddText("x+8 yp w28 h28 Center 0x200 +0x100 c7A3F20 BackgroundEAD2B8", "X")
    PREVIEW_CLOSE_BUTTON.OnEvent("Click", CancelPreview)
    DebugPreviewTest("show_after_header")

    PREVIEW_GUI.SetFont("s9 c7A5A43", "Microsoft YaHei")
    PREVIEW_GUI.AddText("xm y+10 w476 BackgroundFBF3E8", T("hint"))

    if !PREVIEW_SHOW_TABS {
        PREVIEW_GUI.SetFont("s9 c7A5A43", "Microsoft YaHei")
        PREVIEW_GUI.AddText("xm y+10 w34 h26 0x200 BackgroundFBF3E8", T("brand_filter"))
        PREVIEW_BRAND_SELECT := PREVIEW_GUI.AddDropDownList("x+8 yp w142", BuildBrandOptions(PREVIEW_BRANDS))
        PREVIEW_BRAND_SELECT.Choose(BrandOptionIndex(PREVIEW_BRANDS, PREVIEW_SELECTED_BRAND))
        PREVIEW_BRAND_SELECT.OnEvent("Change", PreviewBrandChanged)
        PREVIEW_RETURN_COUNT_SELECT := PREVIEW_GUI.AddDropDownList("x+4 yp w180", BuildReturnCountOptions())
        PREVIEW_RETURN_COUNT_SELECT.Choose(ReturnCountOptionIndex(ReadPreviewReturnCount()))
        PREVIEW_RETURN_COUNT_SELECT.OnEvent("Change", PreviewReturnCountChanged)
    }

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
    PREVIEW_LIST := PREVIEW_GUI.AddListView("xm y+8 w476 h132 Checked -Multi BackgroundFFF9F2 c2F2620", [T("list_header"), "full_text"])
    PREVIEW_LIST.ModifyCol(1, 452)
    PREVIEW_LIST.ModifyCol(2, 0)

    PREVIEW_GUI.SetFont("s9 bold", "Microsoft YaHei")
    sendButton := PREVIEW_GUI.AddText("xm y+14 w110 h32 Center 0x200 +0x100 cFFFFFF BackgroundD97745", T("send_next"))
    cancelButton := PREVIEW_GUI.AddText("x+10 yp w78 h32 Center 0x200 +0x100 c7A5A43 BackgroundF0DDC8", T("cancel"))
    sendButton.OnEvent("Click", SendPreviewNow)
    cancelButton.OnEvent("Click", CancelPreview)

    PREVIEW_MODE_ALL := PREVIEW_GUI.AddText("x0 y0 w1 h1 Hidden", T("send_all"))
    PREVIEW_MODE_SPLIT := PREVIEW_GUI.AddText("x0 y0 w1 h1 Hidden", T("send_split"))
    PREVIEW_MODE_ALL.OnEvent("Click", (*) => SetPreviewMode("all"))
    PREVIEW_MODE_SPLIT.OnEvent("Click", (*) => SetPreviewMode("split"))

    PREVIEW_GUI.OnEvent("Close", CancelPreview)
    PREVIEW_GUI.OnEvent("Escape", CancelPreview)
    DebugPreviewTest("show_after_controls")

    PREVIEW_VISIBLE := true
    RebuildPreviewQueue()
    DebugPreviewTest("show_after_rebuild")

    if (mode = "query_fixed") {
        PositionPreviewAt(mouseX, mouseY)
    } else {
        PositionPreviewNearMouse(mouseX, mouseY)
    }
    DebugPreviewTest("show_after_position")
}

SetPreviewMode(mode) {
    IniWrite mode, CONFIG_PATH, "preview", "send_mode"
    RebuildPreviewQueue()
}

PreviewBrandChanged(*) {
    global PREVIEW_BRAND_SELECT, PREVIEW_BRANDS, PREVIEW_GUI
    global RAG_ORIGINAL_QUESTION, RAG_SELECTED_BRAND, RAG_PREVIEW_X, RAG_PREVIEW_Y

    if !IsObject(PREVIEW_BRAND_SELECT) {
        return
    }

    index := PREVIEW_BRAND_SELECT.Value
    brand := ""
    if (index > 1 && index - 1 <= PREVIEW_BRANDS.Length) {
        brand := PREVIEW_BRANDS[index - 1]
    }
    if (brand = RAG_SELECTED_BRAND) {
        return
    }
    if (Trim(RAG_ORIGINAL_QUESTION) = "") {
        return
    }

    RAG_SELECTED_BRAND := brand
    SaveCurrentPreviewPosition()
    FileDeleteSafe(LAST_SELECTED_PATH)
    FileAppend RAG_ORIGINAL_QUESTION, LAST_SELECTED_PATH, "UTF-8"
    ClosePreview()
    AskRagForSelection()
}

PreviewReturnCountChanged(*) {
    global PREVIEW_RETURN_COUNT_SELECT, RAG_ORIGINAL_QUESTION

    if !IsObject(PREVIEW_RETURN_COUNT_SELECT) {
        return
    }

    count := PREVIEW_RETURN_COUNT_SELECT.Value = 2 ? 10 : 5
    if (count = ReadPreviewReturnCount()) {
        return
    }
    IniWrite count, CONFIG_PATH, "preview", "return_count"
    if (Trim(RAG_ORIGINAL_QUESTION) = "") {
        return
    }

    SaveCurrentPreviewPosition()
    FileDeleteSafe(LAST_SELECTED_PATH)
    FileAppend RAG_ORIGINAL_QUESTION, LAST_SELECTED_PATH, "UTF-8"
    ClosePreview()
    AskRagForSelection()
}

SaveCurrentPreviewPosition() {
    global PREVIEW_GUI, RAG_PREVIEW_X, RAG_PREVIEW_Y

    if IsObject(PREVIEW_GUI) {
        try WinGetPos &RAG_PREVIEW_X, &RAG_PREVIEW_Y,,, "ahk_id " PREVIEW_GUI.Hwnd
    }
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
    global PREVIEW_FALLBACK_MODE

    if !IsObject(PREVIEW_LIST) {
        return
    }

    PREVIEW_LIST.Delete()
    checkedCount := 0
    fallbackText := FallbackManualReply()
    if PREVIEW_FALLBACK_MODE {
        PREVIEW_LIST.Add("Check", MakeListPreview(fallbackText), fallbackText)
        checkedCount += 1
    }
    for index, part in PREVIEW_PARTS {
        if CleanOneLine(part) = fallbackText {
            continue
        }
        options := (PREVIEW_TAB_DEFAULT_CHECKED && ShouldDefaultCheckPart(part)) ? "Check" : ""
        if options = "Check" {
            checkedCount += 1
        }
        PREVIEW_LIST.Add(options, MakeListPreview(part), part)
    }
    if (PREVIEW_TAB_DEFAULT_CHECKED && checkedCount = 0) {
        PREVIEW_LIST.Insert(1, "Check", MakeListPreview(fallbackText), fallbackText)
    }
}

ShouldDefaultCheckPart(text) {
    global PREVIEW_FALLBACK_MODE

    cleaned := CleanOneLine(text)
    if PREVIEW_FALLBACK_MODE {
        return cleaned = FallbackManualReply()
    }
    if InStr(cleaned, "截团") {
        return false
    }
    return true
}

SendPreviewNow(*) {
    SendNextPreviewPart()
}

SendNextPreviewPart() {
    global PREVIEW_LIST, SEND_IN_PROGRESS

    if SEND_IN_PROGRESS {
        return
    }

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
    SEND_IN_PROGRESS := true
    try {
        PasteTextToWeChat(text)
        if !IsObject(PREVIEW_LIST) {
            SEND_IN_PROGRESS := false
            return
        }
        PREVIEW_LIST.Delete(row)

        if CountCheckedRows() = 0 {
            SEND_IN_PROGRESS := false
            ClosePreview()
            return
        }
    } catch as exc {
        SEND_IN_PROGRESS := false
        throw exc
    }
    SEND_IN_PROGRESS := false
}

CancelPreview(*) {
    ClosePreview()
}

ClosePreview(*) {
    global PREVIEW_GUI, PREVIEW_VISIBLE, PREVIEW_TARGET_HWND, PREVIEW_SOURCE_TEXT
    global PREVIEW_PARTS, PREVIEW_INDEX, PREVIEW_EDIT, PREVIEW_STATUS, PREVIEW_MODE_ALL, PREVIEW_MODE_SPLIT
    global PREVIEW_LIST, PREVIEW_CLOSE_BUTTON, PREVIEW_TABS, PREVIEW_TAB_TEXTS, PREVIEW_ACTIVE_TAB, PREVIEW_SHOW_TABS
    global PREVIEW_FALLBACK_MODE, SEND_IN_PROGRESS, PREVIEW_BRAND_SELECT, PREVIEW_RETURN_COUNT_SELECT
    global PREVIEW_BRANDS, PREVIEW_SELECTED_BRAND, PREVIEW_FORCE_SPLIT

    if SEND_IN_PROGRESS {
        return
    }

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
    PREVIEW_FALLBACK_MODE := false
    PREVIEW_BRAND_SELECT := 0
    PREVIEW_RETURN_COUNT_SELECT := 0
    PREVIEW_BRANDS := []
    PREVIEW_SELECTED_BRAND := ""
    PREVIEW_FORCE_SPLIT := false
    PREVIEW_MODE_ALL := 0
    PREVIEW_MODE_SPLIT := 0
    PREVIEW_CLOSE_BUTTON := 0
}

PasteTextToWeChat(text) {
    global LAST_SEND_TICK, MIN_SEND_INTERVAL_MS, PASTE_ONLY_MODE

    testMode := PASTE_ONLY_MODE || ReadBoolConfig("send", "test_mode", false)
    imagePath := ExtractImagePath(text)
    textToSend := RemoveImageLines(text)
    if (Trim(textToSend) = "" && imagePath = "") {
        ShowTip("消息为空")
        return
    }

    WaitForSendInterval()
    oldClipboard := ClipboardAll()

    if !FocusSendBox() {
        ShowTip("未能自动定位微信发送框，已取消粘贴")
        return
    }
    if (Trim(textToSend) != "") {
        A_Clipboard := textToSend
        Sleep ReadIntConfig("send", "clipboard_settle_ms", 80)
        Send "^v"
        Sleep ReadIntConfig("send", "after_text_paste_ms", 180)
    }

    if (imagePath != "" && FileExist(imagePath)) {
        if SetClipboardImage(imagePath) {
            Sleep ImageClipboardSettleMs(imagePath)
            Send "^v"
            Sleep ImagePasteWaitMs(imagePath)
            if !testMode && ReadBoolConfig("send", "send_image_before_text", false) {
                Send "{Enter}"
                Sleep ReadIntConfig("send", "after_image_enter_ms", 500)
            }
        } else {
            ShowTip("图片复制失败")
        }
    }

    if !testMode && ReadBoolConfig("send", "press_enter", true) && (Trim(textToSend) != "" || imagePath != "") {
        Sleep ReadIntConfig("send", "before_enter_ms", 120)
        Send "{Enter}"
    }

    if ReadBoolConfig("send", "restore_clipboard_after_paste", false) {
        A_Clipboard := oldClipboard
    }
    LAST_SEND_TICK := A_TickCount
    ShowTip(testMode ? "测试模式：已粘贴，未发送" : "已粘贴到发送框")
}

ImageClipboardSettleMs(imagePath) {
    baseMs := ReadIntConfig("send", "clipboard_settle_ms", 80)
    per100KbMs := ReadIntConfig("send", "image_clipboard_settle_ms_per_100kb", 70)
    maxMs := ReadIntConfig("send", "image_clipboard_settle_max_ms", 1500)
    sizeKb := ImageFileSizeKb(imagePath)
    if sizeKb <= 0 {
        return baseMs
    }
    return Clamp(baseMs + Ceil(sizeKb / 100) * per100KbMs, baseMs, maxMs)
}

ImagePasteWaitMs(imagePath) {
    baseMs := ReadIntConfig("send", "after_image_paste_ms", 900)
    per100KbMs := ReadIntConfig("send", "after_image_paste_ms_per_100kb", 300)
    maxMs := ReadIntConfig("send", "after_image_paste_max_ms", 6500)
    sizeKb := ImageFileSizeKb(imagePath)
    if sizeKb <= 0 {
        return baseMs
    }
    return Clamp(baseMs + Ceil(sizeKb / 100) * per100KbMs, baseMs, maxMs)
}

ImageFileSizeKb(imagePath) {
    try {
        return Ceil(FileGetSize(ResolveProjectPath(imagePath)) / 1024)
    } catch {
        return 0
    }
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

    try {
        hBitmap := LoadPicture(imagePath)
    } catch {
        return false
    }
    if !hBitmap {
        return false
    }
    hDib := BitmapToDib(hBitmap)
    DllCall("DeleteObject", "UPtr", hBitmap)
    if !hDib {
        return false
    }
    if !OpenClipboardWithRetry(0) {
        DllCall("GlobalFree", "UPtr", hDib)
        return false
    }
    DllCall("EmptyClipboard")
    if !DllCall("SetClipboardData", "UInt", 8, "UPtr", hDib, "UPtr") {
        DllCall("CloseClipboard")
        DllCall("GlobalFree", "UPtr", hDib)
        return false
    }
    DllCall("CloseClipboard")
    return true
}

BitmapToDib(hBitmap) {
    bitmap := Buffer(32, 0)
    if !DllCall("GetObject", "UPtr", hBitmap, "Int", bitmap.Size, "Ptr", bitmap.Ptr) {
        return 0
    }
    width := NumGet(bitmap, 4, "Int")
    height := NumGet(bitmap, 8, "Int")
    if (width <= 0 || height <= 0) {
        return 0
    }

    bitCount := 32
    headerSize := 40
    stride := ((width * bitCount + 31) // 32) * 4
    imageSize := stride * height
    dibSize := headerSize + imageSize
    hDib := DllCall("GlobalAlloc", "UInt", 0x42, "UPtr", dibSize, "UPtr")
    if !hDib {
        return 0
    }
    dibPtr := DllCall("GlobalLock", "UPtr", hDib, "UPtr")
    if !dibPtr {
        DllCall("GlobalFree", "UPtr", hDib)
        return 0
    }

    NumPut("UInt", headerSize, dibPtr, 0)
    NumPut("Int", width, dibPtr, 4)
    NumPut("Int", height, dibPtr, 8)
    NumPut("UShort", 1, dibPtr, 12)
    NumPut("UShort", bitCount, dibPtr, 14)
    NumPut("UInt", 0, dibPtr, 16)
    NumPut("UInt", imageSize, dibPtr, 20)
    NumPut("Int", 0, dibPtr, 24)
    NumPut("Int", 0, dibPtr, 28)
    NumPut("UInt", 0, dibPtr, 32)
    NumPut("UInt", 0, dibPtr, 36)

    hdc := DllCall("CreateCompatibleDC", "UPtr", 0, "UPtr")
    ok := hdc && DllCall(
        "GetDIBits",
        "UPtr", hdc,
        "UPtr", hBitmap,
        "UInt", 0,
        "UInt", height,
        "UPtr", dibPtr + headerSize,
        "UPtr", dibPtr,
        "UInt", 0
    )
    if hdc {
        DllCall("DeleteDC", "UPtr", hdc)
    }
    DllCall("GlobalUnlock", "UPtr", hDib)
    if !ok {
        DllCall("GlobalFree", "UPtr", hDib)
        return 0
    }
    return hDib
}

OpenClipboardWithRetry(hwnd := 0, attempts := 8, delayMs := 35) {
    Loop attempts {
        if DllCall("OpenClipboard", "UPtr", hwnd) {
            return true
        }
        Sleep delayMs
    }
    return false
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
    ResetSendBoxDebug()
    SendBoxDebug("target_hwnd=" targetHwnd)
    try {
        WinGetPos &debugX, &debugY, &debugW, &debugH, "ahk_id " targetHwnd
        SendBoxDebug("window_rect=" debugX "," debugY "," debugW "," debugH)
        SendBoxDebug("window_dpi=" DllCall("GetDpiForWindow", "ptr", targetHwnd, "uint"))
        SendBoxDebug("window_title=" WinGetTitle("ahk_id " targetHwnd))
        SendBoxDebug("window_class=" WinGetClass("ahk_id " targetHwnd))
    }

    if !ReadBoolConfig("send", "click_before_paste", true) {
        SendBoxDebug("skip_focus=click_before_paste_disabled")
        return true
    }

    locatorMode := NormalizeLocatorMode(IniRead(CONFIG_PATH, "send", "locator_mode", "uia"))
    SendBoxDebug("locator_mode=" locatorMode)
    if (locatorMode = "f8") {
        SendBoxDebug("locator=f8_saved_point")
        GetInputAnchor(targetHwnd, &anchorX, &anchorY)
        Click anchorX, anchorY
        Sleep ReadIntConfig("send", "after_click_ms", 100)
        return true
    }

    if TryFocusSendBoxByUIAutomation(targetHwnd) || TryFocusSendBoxByControl(targetHwnd) {
        Sleep ReadIntConfig("send", "after_click_ms", 100)
        return true
    }

    if ReadBoolConfig("send", "allow_safe_geometry_fallback", true) && TryFocusSendBoxBySafeGeometry(targetHwnd) {
        Sleep ReadIntConfig("send", "after_click_ms", 100)
        return true
    }

    if ReadBoolConfig("send", "allow_saved_point_fallback", false) {
        SendBoxDebug("fallback=saved_point")
        GetInputAnchor(targetHwnd, &anchorX, &anchorY)
        Click anchorX, anchorY
        Sleep ReadIntConfig("send", "after_click_ms", 100)
        return true
    }

    SendBoxDebug("result=not_found")
    return false
}

NormalizeLocatorMode(value) {
    mode := StrLower(Trim(value))
    if mode = "f8" || mode = "saved_point" || mode = "saved-point" || mode = "point" {
        return "f8"
    }
    return "uia"
}

FindWeChatWindow() {
    for hwnd in WinGetList() {
        if IsWeChatWindow(hwnd) {
            return hwnd
        }
    }
    return 0
}

PositionPreviewNearMouse(mouseX := "", mouseY := "") {
    global PREVIEW_GUI, PREVIEW_EDIT, PREVIEW_CLOSE_BUTTON, RAG_PREVIEW_X, RAG_PREVIEW_Y

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
    try WinGetPos &RAG_PREVIEW_X, &RAG_PREVIEW_Y,,, "ahk_id " PREVIEW_GUI.Hwnd
    DebugPreviewTest("position_after_show")
    ClearPreviewSelection()
    ApplyRoundedPreviewRegion()
    DebugPreviewTest("position_after_region")
}

PositionPreviewAt(x, y) {
    global PREVIEW_GUI, RAG_PREVIEW_X, RAG_PREVIEW_Y

    if !IsObject(PREVIEW_GUI) {
        return
    }

    if (x = "" || y = "") {
        PositionPreviewNearMouse()
        return
    }

    GetWorkAreaForPoint(x, y, &left, &top, &right, &bottom)
    PREVIEW_GUI.Show("x" x " y" y " AutoSize")
    Sleep 30
    try {
        WinGetPos ,, &previewW, &previewH, "ahk_id " PREVIEW_GUI.Hwnd
        x := Clamp(x, left + 10, right - previewW - 10)
        y := Clamp(y, top + 10, bottom - previewH - 10)
        PREVIEW_GUI.Show("x" x " y" y " AutoSize")
        Sleep 30
        WinGetPos &RAG_PREVIEW_X, &RAG_PREVIEW_Y,,, "ahk_id " PREVIEW_GUI.Hwnd
    } catch {
        RAG_PREVIEW_X := x
        RAG_PREVIEW_Y := y
    }
    ClearPreviewSelection()
    ApplyRoundedPreviewRegion()
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
    clientX := ReadIntConfig("send", "input_client_x", -1)
    clientY := ReadIntConfig("send", "input_client_y", -1)
    savedClientW := ReadIntConfig("send", "input_client_w", 0)
    savedClientH := ReadIntConfig("send", "input_client_h", 0)
    clientRatioX := ReadFloatConfig("send", "input_client_ratio_x", -1)
    clientRatioY := ReadFloatConfig("send", "input_client_ratio_y", -1)
    if GetClientSize(hwnd, &clientW, &clientH) && clientW > 0 && clientH > 0 {
        hasClientPoint := clientX >= 0 && clientY >= 0 && clientX <= clientW && clientY <= clientH
        hasClientRatio := clientRatioX >= 0 && clientRatioX <= 1 && clientRatioY >= 0 && clientRatioY <= 1
        clientSizeChanged := savedClientW > 0 && savedClientH > 0
            && (Abs(clientW - savedClientW) > 80 || Abs(clientH - savedClientH) > 80)
        if hasClientPoint || hasClientRatio {
            pointX := hasClientRatio && clientSizeChanged ? Floor(clientW * clientRatioX) : clientX
            pointY := hasClientRatio && clientSizeChanged ? Floor(clientH * clientRatioY) : clientY
            if ClientPointToScreen(hwnd, pointX, pointY, &anchorX, &anchorY) {
                SendBoxDebug("f8_client_hit=" anchorX "," anchorY ",client=" pointX "," pointY ",size=" clientW "," clientH)
                return
            }
        }
    }

    WinGetPos &winX, &winY, &winW, &winH, "ahk_id " hwnd
    inputX := ReadIntConfig("send", "input_x", 520)
    inputY := ReadIntConfig("send", "input_y", 690)
    ratioX := ReadFloatConfig("send", "input_ratio_x", -1)
    ratioY := ReadFloatConfig("send", "input_ratio_y", -1)
    savedWinW := ReadIntConfig("send", "input_window_w", 0)
    savedWinH := ReadIntConfig("send", "input_window_h", 0)
    canUseRatio := ratioX >= 0 && ratioX <= 1 && ratioY >= 0 && ratioY <= 1
    windowSizeChanged := savedWinW > 0 && savedWinH > 0
        && (Abs(winW - savedWinW) > 80 || Abs(winH - savedWinH) > 80)

    if canUseRatio && (IsBadInputPoint(inputX, inputY, winW, winH) || windowSizeChanged) {
        anchorX := winX + Clamp(Floor(winW * ratioX), 20, Max(20, winW - 20))
        anchorY := winY + Clamp(Floor(winH * ratioY), 20, Max(20, winH - 20))
        return
    }

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

TryFocusSendBoxByControl(hwnd) {
    try {
        controls := WinGetControlsHwnd("ahk_id " hwnd)
        WinGetPos &winX, &winY, &winW, &winH, "ahk_id " hwnd
    } catch {
        SendBoxDebug("control_scan=failed")
        return false
    }
    SendBoxDebug("control_count=" controls.Length)

    bestHwnd := 0
    bestScore := -1
    bestX := 0
    bestY := 0
    bestW := 0
    bestH := 0
    for controlHwnd in controls {
        try {
            className := WinGetClass("ahk_id " controlHwnd)
            style := WinGetStyle("ahk_id " controlHwnd)
            if !(style & 0x10000000) || (style & 0x08000000) {
                continue
            }
            WinGetPos &ctrlX, &ctrlY, &ctrlW, &ctrlH, "ahk_id " controlHwnd
        } catch {
            continue
        }

        score := SendBoxControlScore(className, ctrlX, ctrlY, ctrlW, ctrlH, winX, winY, winW, winH)
        if score > bestScore {
            bestScore := score
            bestHwnd := controlHwnd
            bestX := ctrlX
            bestY := ctrlY
            bestW := ctrlW
            bestH := ctrlH
        }
    }
    SendBoxDebug("control_best_score=" bestScore)

    if !bestHwnd || bestScore < 100 {
        return false
    }

    try {
        ControlFocus "ahk_id " bestHwnd
        ClickSendBoxRect(bestX, bestY, bestW, bestH)
        SendBoxDebug("control_hit=" bestX "," bestY "," bestW "," bestH)
        return true
    } catch {
        SendBoxDebug("control_focus=failed")
        return false
    }
}

SendBoxControlScore(className, x, y, w, h, winX, winY, winW, winH) {
    if w < 40 || h < 18 {
        return -1
    }

    classLower := StrLower(className)
    isEditor := InStr(classLower, "edit")
        || InStr(classLower, "richedit")
        || InStr(classLower, "scintilla")
        || InStr(classLower, "textbox")
    if !IsLikelySendBoxRect(x, y, w, h, winX, winY, winW, winH) {
        return -1
    }

    relBottom := winH > 0 ? (y + h - winY) / winH : 0
    relWidth := winW > 0 ? w / winW : 0
    relHeight := winH > 0 ? h / winH : 0
    score := isEditor ? 120 : 85
    score += Round(relBottom * 80)
    score += Round(Min(relWidth, 1) * 60)
    score += relHeight <= 0.4 ? 25 : -25
    return score
}

TryFocusSendBoxByUIAutomation(hwnd) {
    try {
        uia := CreateUIAutomation()
        root := uia.ElementFromHandle(hwnd)
        condition := uia.CreateTrueCondition()
        elements := root.FindAll(4, condition)
    } catch as exc {
        SendBoxDebug("uia_scan=failed:" exc.Message)
        return false
    }

    try WinGetPos &winX, &winY, &winW, &winH, "ahk_id " hwnd
    catch {
        return false
    }

    bestElement := 0
    bestScore := -1
    bestX := 0
    bestY := 0
    bestW := 0
    bestH := 0
    try count := elements.Length
    catch {
        SendBoxDebug("uia_count=failed")
        return false
    }
    SendBoxDebug("uia_count=" count)

    Loop count {
        try {
            element := elements.GetElement(A_Index - 1)
            if element.CurrentIsOffscreen {
                continue
            }
            if !TryGetUIARect(element, &rectX, &rectY, &rectW, &rectH) {
                continue
            }
            controlType := element.CurrentControlType
            name := ""
            try name := element.CurrentName
            score := SendBoxElementScore(controlType, name, rectX, rectY, rectW, rectH, winX, winY, winW, winH)
        } catch {
            continue
        }

        if score > bestScore {
            bestScore := score
            bestElement := element
            bestX := rectX
            bestY := rectY
            bestW := rectW
            bestH := rectH
        }
    }
    SendBoxDebug("uia_best_score=" bestScore)

    if !IsObject(bestElement) || bestScore < 100 {
        return false
    }

    try {
        bestElement.SetFocus()
        ClickSendBoxRect(bestX, bestY, bestW, bestH)
        SendBoxDebug("uia_hit=" bestX "," bestY "," bestW "," bestH)
        return true
    } catch {
        SendBoxDebug("uia_focus=failed")
        return false
    }
}

CreateUIAutomation() {
    try {
        return ComObject("UIAutomationClient.CUIAutomation")
    } catch {
        return ComObject("{ff48dba4-60ef-4201-aa87-54103eef594e}", "{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}")
    }
}

TryGetUIARect(element, &x, &y, &w, &h) {
    try {
        rect := element.GetCurrentPropertyValue(30001)
        try {
            x := rect[0]
            y := rect[1]
            w := rect[2]
            h := rect[3]
            return w > 0 && h > 0
        }
        try {
            x := rect[1]
            y := rect[2]
            w := rect[3]
            h := rect[4]
            return w > 0 && h > 0
        }
        try {
            x := rect.left
            y := rect.top
            w := rect.right - rect.left
            h := rect.bottom - rect.top
            return w > 0 && h > 0
        }
    }

    try {
        rect := element.CurrentBoundingRectangle
        try {
            x := rect.l
            y := rect.t
            w := rect.r - rect.l
            h := rect.b - rect.t
            return w > 0 && h > 0
        }
        try {
            x := rect.left
            y := rect.top
            w := rect.right - rect.left
            h := rect.bottom - rect.top
            return w > 0 && h > 0
        }
        x := rect[0]
        y := rect[1]
        w := rect[2]
        h := rect[3]
        return w > 0 && h > 0
    } catch {
        return false
    }
}

SendBoxElementScore(controlType, name, x, y, w, h, winX, winY, winW, winH) {
    if w < 40 || h < 18 {
        return -1
    }

    relLeft := winW > 0 ? (x - winX) / winW : 0
    relTop := winH > 0 ? (y - winY) / winH : 0
    relBottom := winH > 0 ? (y + h - winY) / winH : 0
    relWidth := winW > 0 ? w / winW : 0
    relHeight := winH > 0 ? h / winH : 0
    if !IsLikelySendBoxRect(x, y, w, h, winX, winY, winW, winH) {
        return -1
    }

    score := 75
    switch controlType {
        case 50004:
            score += 60
        case 50030, 50033, 50025:
            score += 35
        case 50020:
            score += 10
        default:
            score += 0
    }
    nameLower := StrLower(name)
    if InStr(nameLower, "输入") || InStr(nameLower, "编辑") || InStr(nameLower, "message") || InStr(nameLower, "send") {
        score += 30
    }
    score += Round(relBottom * 90)
    score += Round(Min(relWidth, 1) * 70)
    score += relTop > 0.45 ? 35 : -20
    score += relLeft < 0.55 ? 15 : 0
    score += relHeight <= 0.35 ? 20 : -30
    return score
}

IsLikelySendBoxRect(x, y, w, h, winX, winY, winW, winH) {
    if winW <= 0 || winH <= 0 || w < 80 || h < 24 {
        return false
    }

    relTop := (y - winY) / winH
    relBottom := (y + h - winY) / winH
    relCenterX := (x + w / 2 - winX) / winW
    relWidth := w / winW
    relHeight := h / winH

    return relTop > 0.38
        && relBottom > 0.58
        && relCenterX > 0.38
        && relWidth > 0.16
        && relHeight < 0.38
}

ClickSendBoxRect(x, y, w, h) {
    clickX := x + Floor(w * 0.5)
    clickY := y + Floor(h * 0.55)
    Click clickX, clickY
}

TryFocusSendBoxBySafeGeometry(hwnd) {
    try {
        WinGetPos &winX, &winY, &winW, &winH, "ahk_id " hwnd
    } catch as exc {
        SendBoxDebug("geometry=failed:" exc.Message)
        return false
    }

    if winW < 760 || winH < 520 {
        SendBoxDebug("geometry=skip_small_window:" winW "," winH)
        return false
    }

    chatLeft := Max(Floor(winW * 0.34), 320)
    clickX := winX + chatLeft + Floor((winW - chatLeft) * 0.55)
    clickY := winY + winH - Max(86, Floor(winH * 0.085))

    if clickX < winX + Floor(winW * 0.45) || clickX > winX + winW - 80 {
        SendBoxDebug("geometry=unsafe_x:" clickX)
        return false
    }
    if clickY < winY + Floor(winH * 0.65) || clickY > winY + winH - 35 {
        SendBoxDebug("geometry=unsafe_y:" clickY)
        return false
    }

    Click clickX, clickY
    SendBoxDebug("geometry_hit=" clickX "," clickY)
    return true
}

ResetSendBoxDebug() {
    global SEND_BOX_DEBUG_PATH

    if !ReadBoolConfig("send", "sendbox_debug", true) {
        return
    }
    try FileDelete SEND_BOX_DEBUG_PATH
    SendBoxDebug("started=" A_Now)
}

SendBoxDebug(message) {
    global SEND_BOX_DEBUG_PATH

    if !ReadBoolConfig("send", "sendbox_debug", true) {
        return
    }
    try FileAppend message "`n", SEND_BOX_DEBUG_PATH, "UTF-8"
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
    targetHwnd := GetCalibrationWindowAtPoint(mouseX, mouseY)
    if !targetHwnd {
        targetHwnd := WinGetID("A")
    }

    WinGetPos &winX, &winY, &winW, &winH, "ahk_id " targetHwnd
    inputX := mouseX - winX
    inputY := mouseY - winY
    inputRatioX := winW > 0 ? Round(inputX / winW, 6) : 0
    inputRatioY := winH > 0 ? Round(inputY / winH, 6) : 0

    hasClientPoint := ScreenPointToClient(targetHwnd, mouseX, mouseY, &clientX, &clientY)
        && GetClientSize(targetHwnd, &clientW, &clientH)
    if hasClientPoint {
        clientRatioX := clientW > 0 ? Round(clientX / clientW, 6) : 0
        clientRatioY := clientH > 0 ? Round(clientY / clientH, 6) : 0
        IniWrite clientX, CONFIG_PATH, "send", "input_client_x"
        IniWrite clientY, CONFIG_PATH, "send", "input_client_y"
        IniWrite clientRatioX, CONFIG_PATH, "send", "input_client_ratio_x"
        IniWrite clientRatioY, CONFIG_PATH, "send", "input_client_ratio_y"
        IniWrite clientW, CONFIG_PATH, "send", "input_client_w"
        IniWrite clientH, CONFIG_PATH, "send", "input_client_h"
    }

    IniWrite inputX, CONFIG_PATH, "send", "input_x"
    IniWrite inputY, CONFIG_PATH, "send", "input_y"
    IniWrite inputRatioX, CONFIG_PATH, "send", "input_ratio_x"
    IniWrite inputRatioY, CONFIG_PATH, "send", "input_ratio_y"
    IniWrite winW, CONFIG_PATH, "send", "input_window_w"
    IniWrite winH, CONFIG_PATH, "send", "input_window_h"
    ShowTip(hasClientPoint ? "已保存输入框客户区点位: " clientX ", " clientY : "已保存兜底输入框点位: " inputX ", " inputY)
}

GetClientSize(hwnd, &width, &height) {
    rect := Buffer(16, 0)
    if !DllCall("GetClientRect", "ptr", hwnd, "ptr", rect.Ptr) {
        return false
    }
    width := NumGet(rect, 8, "int") - NumGet(rect, 0, "int")
    height := NumGet(rect, 12, "int") - NumGet(rect, 4, "int")
    return true
}

ScreenPointToClient(hwnd, screenX, screenY, &clientX, &clientY) {
    point := Buffer(8, 0)
    NumPut("int", screenX, point, 0)
    NumPut("int", screenY, point, 4)
    if !DllCall("ScreenToClient", "ptr", hwnd, "ptr", point.Ptr) {
        return false
    }
    clientX := NumGet(point, 0, "int")
    clientY := NumGet(point, 4, "int")
    return true
}

ClientPointToScreen(hwnd, clientX, clientY, &screenX, &screenY) {
    point := Buffer(8, 0)
    NumPut("int", clientX, point, 0)
    NumPut("int", clientY, point, 4)
    if !DllCall("ClientToScreen", "ptr", hwnd, "ptr", point.Ptr) {
        return false
    }
    screenX := NumGet(point, 0, "int")
    screenY := NumGet(point, 4, "int")
    return true
}

GetCalibrationWindowAtPoint(mouseX, mouseY) {
    MouseGetPos ,, &mouseHwnd
    if mouseHwnd {
        rootHwnd := DllCall("GetAncestor", "ptr", mouseHwnd, "uint", 2, "ptr")
        if rootHwnd && IsWeChatWindow(rootHwnd) {
            return rootHwnd
        }
        if IsWeChatWindow(mouseHwnd) {
            return mouseHwnd
        }
    }

    activeHwnd := WinGetID("A")
    return IsWeChatWindow(activeHwnd) ? activeHwnd : 0
}

IsWeChatWindow(hwnd) {
    if !hwnd {
        return false
    }

    try {
        exeList := StrSplit(IniRead(CONFIG_PATH, "wechat", "exe_list", "WeChat.exe,Weixin.exe"), ",")
        processName := WinGetProcessName("ahk_id " hwnd)
        for exe in exeList {
            if Trim(exe) = processName {
                return true
            }
        }
    }

    return false
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

ReadQueryBrands() {
    global RAG_BRANDS_PATH

    brands := []
    seen := Map()
    if !FileExist(RAG_BRANDS_PATH) {
        return brands
    }
    text := StripBom(FileRead(RAG_BRANDS_PATH, "UTF-8"))
    normalized := StrReplace(text, "`r`n", "`n")
    normalized := StrReplace(normalized, "`r", "`n")
    for line in StrSplit(normalized, "`n") {
        brand := Trim(line, "`r`n `t")
        if (brand = "" || seen.Has(StrLower(brand))) {
            continue
        }
        seen[StrLower(brand)] := true
        brands.Push(brand)
    }
    return brands
}

BuildBrandOptions(brands) {
    options := [T("no_brand_filter")]
    for brand in brands {
        options.Push(brand)
    }
    return options
}

BrandOptionIndex(brands, selectedBrand) {
    selectedBrand := Trim(selectedBrand)
    if (selectedBrand = "") {
        return 1
    }
    for index, brand in brands {
        if (brand = selectedBrand) {
            return index + 1
        }
    }
    return 1
}

BuildReturnCountOptions() {
    return [T("return_count_5"), T("return_count_10")]
}

ReturnCountOptionText(count) {
    return ReadReturnCountValue(count) = 10 ? T("return_count_10") : T("return_count_5")
}

ReturnCountOptionIndex(count) {
    return ReadReturnCountValue(count) = 10 ? 2 : 1
}

ReadPreviewReturnCount() {
    return ReadReturnCountValue(IniRead(CONFIG_PATH, "preview", "return_count", "5"))
}

ReadReturnCountValue(value) {
    try {
        count := Integer(value)
    } catch {
        count := 5
    }
    return count = 10 ? 10 : 5
}

PreparePreviewSourceText(text) {
    global PREVIEW_FALLBACK_MODE

    PREVIEW_FALLBACK_MODE := false
    text := StripBom(text)
    normalized := StrReplace(text, "`r`n", "`n")
    normalized := StrReplace(normalized, "`r", "`n")
    output := ""
    markerFound := false
    for line in StrSplit(normalized, "`n") {
        trimmed := Trim(line)
        if (!markerFound && trimmed = "__RAG_FUZZY_FALLBACK__") {
            PREVIEW_FALLBACK_MODE := true
            markerFound := true
            continue
        }
        output .= (output = "" ? "" : "`n") line
    }

    output := Trim(output, "`r`n `t")
    if PREVIEW_FALLBACK_MODE {
        fallbackText := FallbackManualReply()
        if !InStr(output, fallbackText) {
            output .= (output = "" ? "" : "`n---`n") fallbackText
        }
    }
    return output
}

FallbackManualReply() {
    return "没做这款的，看看其他的呢"
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
        parts.Push(RemoveLeadingItemNumber(trimmed))
    }
}

RemoveLeadingItemNumber(text) {
    return RegExReplace(text, "^\s*\d+\s*[\.、)]\s*", "")
}

CountCheckedRows() {
    global PREVIEW_LIST

    if !IsObject(PREVIEW_LIST) {
        return 0
    }

    count := 0
    row := 0
    loop {
        if !IsObject(PREVIEW_LIST) {
            return 0
        }
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
    global PREVIEW_FORCE_SPLIT

    if PREVIEW_FORCE_SPLIT {
        return "split"
    }
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
            return "Tab " C(38190) " " C(21457, 36865, 19979, 19968, 26465) ", Esc " C(21462, 28040)
        case "brand_filter":
            return C(21697, 29260)
        case "no_brand_filter":
            return C(26080, 25351, 23450)
        case "return_count_5":
            return C(36820, 22238, 25968, 30446) ": 5"
        case "return_count_10":
            return C(36820, 22238, 25968, 30446) ": 10"
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
return_count=5

[send]
text=
locator_mode=uia
test_mode=0
click_before_paste=1
allow_saved_point_fallback=0
allow_safe_geometry_fallback=1
sendbox_debug=1
input_x=520
input_y=690
input_ratio_x=0.72
input_ratio_y=0.9
input_window_w=0
input_window_h=0
input_client_x=-1
input_client_y=-1
input_client_ratio_x=-1
input_client_ratio_y=-1
input_client_w=0
input_client_h=0
after_click_ms=100
clipboard_settle_ms=80
image_clipboard_settle_ms_per_100kb=70
image_clipboard_settle_max_ms=1500
after_text_paste_ms=180
after_image_paste_ms=900
after_image_paste_ms_per_100kb=300
after_image_paste_max_ms=6500
send_image_before_text=0
after_image_enter_ms=500
before_enter_ms=120
press_enter=1
restore_clipboard_after_paste=0

[rag]
enabled=1
fallback_seconds=5
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

RunPasteImageSelfTest() {
    global PREVIEW_TARGET_HWND, PASTE_ONLY_MODE

    resultPath := A_ScriptDir "\paste-image-test-result.ini"
    imagePath := A_ScriptDir "\..\data\tmp\order_flow_preview\test\row-2-330a5ea49e8d8164.png"
    FileDeleteSafe(resultPath)
    FileAppend "[debug]`nstarted=1`nimage=" imagePath "`nexists=" (FileExist(imagePath) ? "1" : "0") "`n", resultPath, "UTF-8"

    hwnd := FindWeChatWindow()
    FileAppend "wechat_hwnd=" hwnd "`n", resultPath, "UTF-8"
    if !hwnd {
        FileAppend "pasted=0`nerror=wechat_window_not_found`n", resultPath, "UTF-8"
        return
    }
    if !FileExist(imagePath) {
        FileAppend "pasted=0`nerror=image_not_found`n", resultPath, "UTF-8"
        return
    }

    PREVIEW_TARGET_HWND := hwnd
    PASTE_ONLY_MODE := true
    try {
        PasteTextToWeChat("图片：" imagePath)
        FileAppend "pasted=1`n", resultPath, "UTF-8"
    } catch as exc {
        FileAppend "pasted=0`nerror=" exc.Message "`n", resultPath, "UTF-8"
    } finally {
        PASTE_ONLY_MODE := false
        PREVIEW_TARGET_HWND := 0
    }
}

RunSendBoxSelfTest() {
    global PREVIEW_TARGET_HWND

    resultPath := A_ScriptDir "\send-box-test-result.ini"
    marker := "[Customer RAG send-box test " A_Now "]"
    FileDeleteSafe(resultPath)
    FileAppend "[debug]`nstarted=1`nmarker=" marker "`n", resultPath, "UTF-8"

    hwnd := FindWeChatWindow()
    FileAppend "wechat_hwnd=" hwnd "`n", resultPath, "UTF-8"
    if !hwnd {
        FileAppend "focused=0`nverified=0`nerror=wechat_window_not_found`n", resultPath, "UTF-8"
        return
    }

    oldClipboard := ClipboardAll()
    PREVIEW_TARGET_HWND := hwnd
    try {
        focused := FocusSendBox()
        FileAppend "focused=" (focused ? "1" : "0") "`n", resultPath, "UTF-8"
        if !focused {
            FileAppend "verified=0`nerror=focus_failed`n", resultPath, "UTF-8"
            return
        }

        A_Clipboard := marker
        Sleep 120
        Send "^v"
        Sleep 250
        A_Clipboard := ""
        Send "^a"
        Sleep 80
        Send "^c"
        copied := ClipWait(0.8) ? A_Clipboard : ""
        verified := InStr(copied, marker) > 0
        FileAppend "verified=" (verified ? "1" : "0") "`n", resultPath, "UTF-8"
        FileAppend "copied_length=" StrLen(copied) "`n", resultPath, "UTF-8"
        Send "^z"
        Sleep 120
        A_Clipboard := ""
        Send "^a"
        Sleep 80
        Send "^c"
        afterUndo := ClipWait(0.8) ? A_Clipboard : ""
        cleaned := InStr(afterUndo, marker) = 0
        FileAppend "cleanup_verified=" (cleaned ? "1" : "0") "`n", resultPath, "UTF-8"
    } catch as exc {
        FileAppend "verified=0`nerror=" exc.Message "`n", resultPath, "UTF-8"
    } finally {
        A_Clipboard := oldClipboard
        PREVIEW_TARGET_HWND := 0
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

