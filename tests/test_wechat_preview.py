from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "wechatExtension" / "WeChatQuickTool.ahk"
AUTOHOTKEY = ROOT / "wechatExtension" / "tools" / "autohotkey" / "AutoHotkey64.exe"


@pytest.fixture
def run_preview_check(tmp_path: Path):
    if os.name != "nt" or not AUTOHOTKEY.is_file():
        pytest.skip("AutoHotkey v2 is required for the native preview checks")

    def run(body: str) -> None:
        source = SCRIPT.read_text(encoding="utf-8-sig")
        # Run the actual script functions with files isolated from the live
        # extension. Keep native controls hidden and never trigger sending.
        source = source.replace(
            'PREVIEW_GUI.Show("x" x " y" y " AutoSize")',
            'PREVIEW_GUI.Show("Hide x" x " y" y " AutoSize")',
        )
        startup = f"""
DetectHiddenWindows true
try {{
{body}
    ClosePreview()
    ExitApp 0
}} catch as exc {{
    FileAppend exc.Message " at line " exc.Line "`n", "*", "UTF-8"
    ExitApp 1
}}
"""
        source = source.replace("EnsureBootstrapFiles()\nOnError(LogUnhandledRuntimeError)", startup, 1)
        source += """

AssertPreview(value, message) {
    if !value {
        throw Error(message)
    }
}
"""
        harness = tmp_path / "preview-check.ahk"
        harness.write_text(source, encoding="utf-8-sig")
        result = subprocess.run(
            [str(AUTOHOTKEY), "/ErrorStdOut", str(harness)],
            cwd=tmp_path,
            capture_output=True,
            timeout=15,
        )
        assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")

    return run


@pytest.mark.parametrize(
    "markers",
    [
        "__RAG_QUERY_TIMEOUT__",
        "__RAG_QUERY_TIMEOUT__`n__RAG_FUZZY_FALLBACK__",
        "__RAG_FUZZY_FALLBACK__`n__RAG_QUERY_TIMEOUT__",
    ],
)
def test_timeout_preview_removes_markers_and_ignores_old_brands(run_preview_check, markers: str):
    run_preview_check(
        f"""
    FileAppend "海信`n海尔", RAG_BRANDS_PATH, "UTF-8"
    RAG_SELECTED_BRAND := "海信"
    ShowSendPreview("{markers}`n查询超时，请稍后重试", 360, 240, "query_fixed")
    AssertPreview(PREVIEW_QUERY_TIMEOUT, "Timeout state was lost")
    AssertPreview(!PREVIEW_FALLBACK_MODE, "Timeout must take priority over fuzzy fallback")
    AssertPreview(PREVIEW_BRANDS.Length = 0, "Old brands leaked into timeout preview")
    AssertPreview(PREVIEW_SELECTED_BRAND = "", "Old brand selection leaked into timeout preview")
    AssertPreview(PREVIEW_BRAND_SELECT.Text = "查询超时", "Wrong timeout brand label")
    AssertPreview(PREVIEW_SOURCE_TEXT = "查询超时，请稍后重试", "Control markers or no-stock reply leaked")
    AssertPreview(PREVIEW_LIST.GetCount() = 1, "Unexpected timeout reply count")
    AssertPreview(PREVIEW_LIST.GetText(1, 2) = "查询超时，请稍后重试", "Wrong timeout reply")
    AssertPreview(CountCheckedRows() = 1, "Timeout reply should remain selectable")
    PREVIEW_PARTS := []
    UpdatePreviewText()
    AssertPreview(PREVIEW_LIST.GetCount() = 0, "Empty timeout preview inserted a no-stock reply")
"""
    )


def test_empty_result_brand_summary_and_timeout_state_reset(run_preview_check):
    run_preview_check(
        """
    ShowSendPreview("__RAG_QUERY_TIMEOUT__`n查询超时，请稍后重试", 360, 240, "query_fixed")
    ShowSendPreview("__RAG_FUZZY_FALLBACK__`n没有做这款呢，看看其他", 360, 240, "query_fixed")
    AssertPreview(!PREVIEW_QUERY_TIMEOUT, "A previous timeout leaked into the next result")
    AssertPreview(PREVIEW_BRAND_SELECT.Text = "共计0个品牌", "Wrong empty result brand label")
    AssertPreview(PREVIEW_LIST.GetCount() = 1, "Empty result reply was duplicated")
    AssertPreview(PREVIEW_LIST.GetText(1, 2) = FallbackManualReply(), "Wrong empty result reply")
    AssertPreview(CountCheckedRows() = 1, "Empty result reply should remain selectable")
"""
    )


def test_timeout_preserves_partial_candidates_without_no_stock_reply(run_preview_check):
    run_preview_check(
        """
    FileAppend "海信", RAG_BRANDS_PATH, "UTF-8"
    sample := "海信 电视 E8，参考价格 3999 元"
    ShowSendPreview("__RAG_QUERY_TIMEOUT__`n__RAG_FUZZY_FALLBACK__`n" sample, 360, 240, "query_fixed")
    AssertPreview(PREVIEW_BRAND_SELECT.Text = "查询超时", "Wrong partial timeout label")
    AssertPreview(PREVIEW_SOURCE_TEXT = sample, "Partial candidate was altered")
    AssertPreview(PREVIEW_LIST.GetCount() = 1, "No-stock reply was added to partial candidates")
    AssertPreview(PREVIEW_LIST.GetText(1, 2) = sample, "Partial candidate disappeared")
    AssertPreview(CountCheckedRows() = 1, "Partial candidate should remain selectable")
"""
    )


def test_external_timeout_writes_timeout_marker_and_clears_brand_file(run_preview_check):
    run_preview_check(
        """
    FileAppend "海信`n海尔", RAG_BRANDS_PATH, "UTF-8"
    FileAppend "旧查询结果", SEND_TEXT_PATH, "UTF-8"
    WriteRagFallbackResult()
    AssertPreview(!FileExist(RAG_BRANDS_PATH), "External timeout kept stale brands")
    AssertPreview(ReadSendText() = "__RAG_QUERY_TIMEOUT__`n查询超时，请稍后重试", "Wrong external timeout result")
    ShowSendPreview(ReadSendText(), 360, 240, "query_fixed")
    AssertPreview(PREVIEW_BRAND_SELECT.Text = "查询超时", "External timeout has the wrong label")
    AssertPreview(PREVIEW_LIST.GetText(1, 2) = "查询超时，请稍后重试", "External timeout leaked a no-stock reply")
"""
    )


def test_successful_brand_options_are_unchanged(run_preview_check):
    run_preview_check(
        """
    FileAppend "海信`n海尔", RAG_BRANDS_PATH, "UTF-8"
    RAG_SELECTED_BRAND := "海信"
    ShowSendPreview("海信 电视 E8，参考价格 3999 元", 360, 240, "query_fixed")
    AssertPreview(!PREVIEW_QUERY_TIMEOUT, "Successful result was marked as timeout")
    AssertPreview(PREVIEW_BRANDS.Length = 2, "Successful brands were discarded")
    AssertPreview(PREVIEW_BRAND_SELECT.Text = "海信", "Selected brand was lost")
    PREVIEW_BRAND_SELECT.Choose(1)
    AssertPreview(PREVIEW_BRAND_SELECT.Text = "共计2个品牌", "Wrong successful brand summary")
"""
    )


@pytest.mark.parametrize("first_match", range(1, 9))
def test_talk_preview_selects_first_matching_category_in_existing_order(run_preview_check, first_match):
    run_preview_check(
        f"""
    texts := []
    Loop 8 {{
        texts.Push("没有做这个的，看看别的渠道")
    }}
    texts[{first_match}] := "匹配的文档 https://docs.qq.com/sheet/current"
    if ({first_match} < 8) {{
        texts[8] := "另一个分类的匹配结果"
    }}
    serialized := JoinShortcutParts(texts)
    FileAppend serialized, RAG_TALK_SHORTCUTS_PATH, "UTF-8"
    ShowSendPreview(texts[2], 360, 240, "talk_shortcuts")
    AssertPreview(PREVIEW_ACTIVE_TAB = {first_match}, "Did not select first matching category")
    AssertPreview(JoinShortcutParts(PREVIEW_TAB_TEXTS) = serialized, "Category order changed")
    AssertPreview(PREVIEW_LIST.GetText(1, 2) = texts[{first_match}], "Wrong initial reply")
    AssertPreview(CountCheckedRows() = 1, "Matching reply should be checked")
    SetPreviewShortcut(1)
    AssertPreview(PREVIEW_ACTIVE_TAB = 1, "Manual selection was overridden")
"""
    )


def test_talk_preview_all_empty_or_fallback_keeps_default_category(run_preview_check):
    run_preview_check(
        """
    texts := ["没有做这个的，看看别的渠道", "", "  `r`n`t", "---", "", "", "", ""]
    FileAppend JoinShortcutParts(texts), RAG_TALK_SHORTCUTS_PATH, "UTF-8"
    ShowSendPreview("", 360, 240, "talk_shortcuts")
    AssertPreview(PREVIEW_ACTIVE_TAB = 1, "No results must keep the default category")
"""
    )


def test_talk_preview_handles_missing_shortcut_file_and_resets_each_search(run_preview_check):
    run_preview_check(
        """
    ShowSendPreview("品牌文档链接", 360, 240, "talk_shortcuts")
    AssertPreview(PREVIEW_ACTIVE_TAB = 2, "Realtime-only fallback should select realtime")
    AssertPreview(PREVIEW_LIST.GetText(1, 2) = "品牌文档链接", "Realtime result missing")
    ShowSendPreview("没有做这个的，看看别的渠道", 360, 240, "talk_shortcuts")
    AssertPreview(PREVIEW_ACTIVE_TAB = 1, "Previous selection leaked into empty search")
    ShowSendPreview("普通 Tab 查询结果", 360, 240, "query_fixed")
    AssertPreview(PREVIEW_ACTIVE_TAB = 1, "Standard query selection changed")
"""
    )


def test_talk_preview_image_result_counts_as_match(run_preview_check):
    run_preview_check(
        """
    texts := ["", "没有做这个的，看看别的渠道", "", "", "素材：对比图`nimage: comparison.png", "", "", ""]
    FileAppend JoinShortcutParts(texts), RAG_TALK_SHORTCUTS_PATH, "UTF-8"
    ShowSendPreview(texts[2], 360, 240, "talk_shortcuts")
    AssertPreview(PREVIEW_ACTIVE_TAB = 5, "Image result was not selected")
    AssertPreview(PREVIEW_LIST.GetText(1, 2) = texts[5], "Image reply missing")
"""
    )
