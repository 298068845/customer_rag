from __future__ import annotations

import sys

if hasattr(sys, "_base_executable"):
    sys._base_executable = sys.executable


if __name__ == "__main__":
    from streamlit.web import bootstrap

    bootstrap.run(
        "app.py",
        False,
        [],
        {
            "server.address": "127.0.0.1",
            "server.port": 8501,
            "server.headless": True,
            "browser.gatherUsageStats": False,
        },
    )
