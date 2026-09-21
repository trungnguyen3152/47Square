"""Run an end-to-end routing smoke test for every UI integration."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from ai_status_core import load_config, send_to_bridge
from ai_status_hook import set_hook_status
from ai_status_integrations import INTEGRATIONS, integration_state
from ai_status_projects import canonical_project, resolve_event_project


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"


def write_config(config: dict) -> None:
    temporary = CONFIG_PATH.with_name(f"config.json.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(CONFIG_PATH)


def main() -> int:
    original = load_config()
    project = canonical_project(ROOT)
    port = str(original["devices"][0]["port"] if original.get("devices") else original["serial_port"])
    results: list[tuple[str, bool, str]] = []
    try:
        for integration in INTEGRATIONS:
            provider = integration.provider
            state, message = integration_state(provider)
            test_config = dict(original)
            test_config["devices"] = [{
                "id": f"matrix-{port.casefold()}",
                "name": "ESP32 matrix test",
                "port": port,
                "provider": provider,
                "project": project,
            }]
            write_config(test_config)
            resolved = resolve_event_project(provider, {}, [project])
            if resolved != project:
                results.append((provider, False, "project fallback failed"))
                continue
            try:
                set_hook_status("YELLOW", provider, project)
                time.sleep(0.35)
                set_hook_status("GREEN", provider, project)
                time.sleep(0.35)
            except Exception as exc:
                results.append((provider, False, str(exc)))
            else:
                results.append((provider, state == "active", message))
    finally:
        write_config(original)
        send_to_bridge("RED", original, target_port=port)

    for provider, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'} {provider}: {detail}")
    return 0 if len(results) == len(INTEGRATIONS) and all(item[1] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
