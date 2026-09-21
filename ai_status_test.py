"""Manual hardware smoke test; this file is not an automated unit test."""

from __future__ import annotations

import argparse
import time

from ai_status_core import load_config, send_to_bridge


def main() -> int:
    parser = argparse.ArgumentParser(description="Cycle through all light states")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--config")
    args = parser.parse_args()

    config = load_config(args.config)
    for status in ("red", "yellow", "green", "off"):
        send_to_bridge(status, config)
        print(f"Sent {status.upper()}")
        time.sleep(args.delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
