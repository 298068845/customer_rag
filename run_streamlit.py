from __future__ import annotations

import sys

from customer_rag.logging_config import configure_logging, install_exception_hooks

if hasattr(sys, "_base_executable"):
    sys._base_executable = sys.executable


if __name__ == "__main__":
    configure_logging(component="app")
    install_exception_hooks(component="app")
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
