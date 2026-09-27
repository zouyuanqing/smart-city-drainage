#!/usr/bin/env python3
"""Dependency-free HTTP probe used by the Compose liveness/readiness checks."""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request

DEFAULT_TIMEOUT_SECONDS = 5.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return success only for an HTTP 2xx response.")
    parser.add_argument("--url", required=True, help="Absolute health/readiness URL")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Request timeout in seconds",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout <= 0:
        print("healthcheck timeout must be positive", file=sys.stderr)
        return 2

    request = urllib.request.Request(args.url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            status = response.status
            response.read(1)
    except urllib.error.HTTPError as exc:
        print(f"healthcheck failed: HTTP {exc.code}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"healthcheck failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    if not 200 <= status < 300:
        print(f"healthcheck failed: HTTP {status}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
