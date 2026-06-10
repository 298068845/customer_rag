# WeChat RAG workflow

Start the tray launcher from this project root:

```powershell
.\start_wechat_rag.ps1
```

You can also double-click:

```text
start_launcher.bat
```

The launcher appears in the Windows bottom-right tray area. Right-click the tray icon to:

- open the RAG page
- turn the WeChat plugin on
- turn the WeChat plugin off
- choose the send-box locating mode: `UIA 定位` by default, or `F8 定位` for the saved point
- restart the RAG service
- exit the launcher

Flow:

1. In WeChat, select the customer question and press `Tab`.
2. A floating loading prompt appears while the local RAG system is generating the reply.
3. When the query completes, the floating prompt changes to a completion reminder.
4. The answer is written to `wechatExtension/send-text.txt`.
5. Multiple answer segments are separated by a standalone `---` line.
6. Press `~` to open the send preview. The first tab is the RAG query result. Custom tabs read from `wechatExtension/custom-tab-2.txt` through `wechatExtension/custom-tab-5.txt` and are not checked by default.
7. Keep pressing `~` to send each checked segment to WeChat.
8. By default, the plugin focuses the WeChat send box through Windows UI Automation and window controls. To use the saved `F8` point instead, right-click the bottom-right tray icon and switch `定位模式` to `F8 定位`.

The old stop script still stops only the WeChat hotkey tool:

```powershell
.\stop_wechat_rag.ps1
```

Logs:

- `wechatExtension/rag-bridge.log`
- `streamlit.log`
- `streamlit.err.log`
