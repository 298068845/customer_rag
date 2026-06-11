from __future__ import annotations

import json
import sys
from pathlib import Path

from customer_rag.config import load_config
from customer_rag.cookie_login import load_saved_cookie
from customer_rag.raw_jobs import _run_raw_job
from customer_rag.subscription_jobs import _run_subscription_job
from customer_rag.tencent_docs import TencentDocSubscription


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if not args:
        return 2
    config = load_config()
    kind = args.pop(0)
    if kind == "raw":
        if len(args) != 3:
            return 2
        job_id, task, scope = args
        _run_raw_job(config, task, job_id, scope)
        return 0
    if kind == "subscription":
        if len(args) != 3:
            return 2
        job_id, request_path_text, origin = args
        request_path = Path(request_path_text)
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        subscriptions = [
            TencentDocSubscription(
                name=str(item.get("name", "")),
                url=str(item.get("url", "")),
                tags=[str(tag) for tag in item.get("tags", [])],
                enabled=bool(item.get("enabled", True)),
                last_updated=str(item.get("last_updated", "")),
                last_status=str(item.get("last_status", "")),
                last_modified=str(item.get("last_modified", "")),
            )
            for item in payload.get("subscriptions", [])
        ]
        _run_subscription_job(
            config,
            Path(payload["subscriptions_path"]),
            subscriptions,
            load_saved_cookie(config),
            job_id,
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
