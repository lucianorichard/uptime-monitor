"""Run the monitor and its dashboard: uptime-monitor --config config.toml"""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from .app import create_app
from .config import ConfigError, load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="uptime-monitor", description=__doc__)
    parser.add_argument("-c", "--config", default="config.toml", help="TOML configuration file")
    # Local-only by default: the dashboard has no authentication.
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = load_config(args.config)
    except ConfigError as exc:
        print(f"uptime-monitor: invalid configuration: {exc}", file=sys.stderr)
        return 2
    logging.getLogger("uptime_monitor").info(
        "monitoring %d target(s) every %ds", len(settings.targets), settings.interval_seconds
    )
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
