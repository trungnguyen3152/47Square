"""Command-line client for the AI status light."""

from __future__ import annotations

import argparse
import sys

from ai_status_core import StatusError, load_config, send_direct, send_to_bridge


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Set the AI status light")
    parser.add_argument("status", choices=("red", "yellow", "green", "off"))
    parser.add_argument(
        "--direct",
        action="store_true",
        help="write to the serial port instead of using the bridge",
    )
    parser.add_argument("--config", help="path to a JSON configuration file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.direct:
            send_direct(args.status, config)
            transport = "serial"
        else:
            try:
                send_to_bridge(args.status, config)
                transport = "bridge"
            except OSError:
                send_direct(args.status, config)
                transport = "serial fallback"
    except (OSError, StatusError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Sent {args.status.upper()} via {transport}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
