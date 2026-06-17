from __future__ import annotations

import sys

from customer_rag.logging_config import configure_logging, install_exception_hooks

if hasattr(sys, "_base_executable"):
    sys._base_executable = sys.executable


if __name__ == "__main__":
    configure_logging(component="app")
    install_exception_hooks(component="app")
    from streamlit import config
    from streamlit.web import bootstrap

    config.set_option("server.address", "127.0.0.1")
    config.set_option("server.port", 8502)
    config.set_option("server.headless", True)
    config.set_option("browser.gatherUsageStats", False)
    bootstrap.run(
        "talk_app.py",
        False,
        [],
        {},
    )
