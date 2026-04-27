#!/usr/bin/env python3
"""CLI health check script for the MCP System.

Performs a deep health check against a running MCP API instance and
reports the status of all subsystems (Redis, Qdrant, modules).

Usage:
    python scripts/health_check.py
    python scripts/health_check.py --url http://localhost:8000
    python scripts/health_check.py --url http://prod.mcp.internal --api-key mcp_xxx
    python scripts/health_check.py --json   # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

try:
    import httpx
except ImportError:
    print("httpx not installed. Run: pip install httpx", file=sys.stderr)
    sys.exit(1)


ANSI_GREEN  = "\033[32m"
ANSI_RED    = "\033[31m"
ANSI_YELLOW = "\033[33m"
ANSI_RESET  = "\033[0m"
ANSI_BOLD   = "\033[1m"


def _color(text: str, color: str) -> str:
    return f"{color}{text}{ANSI_RESET}"


def _status_icon(status: str) -> str:
    if status == "healthy":
        return _color("✓", ANSI_GREEN)
    if status in ("degraded", "unknown"):
        return _color("~", ANSI_YELLOW)
    return _color("✗", ANSI_RED)


def check_health(base_url: str, api_key: str | None, timeout: int) -> dict[str, Any]:
    headers: dict[str, str] = {}
    if api_key:
        headers["X-API-Key"] = api_key

    with httpx.Client(base_url=base_url, headers=headers, timeout=timeout) as client:
        resp = client.get("/health/")
        resp.raise_for_status()
        return resp.json()


def print_human(data: dict[str, Any], base_url: str) -> int:
    envelope = data.get("data", data)
    overall   = envelope.get("status", "unknown")
    version   = envelope.get("version", "?")
    env_name  = envelope.get("environment", "?")
    checks    = envelope.get("checks", {})

    print(f"\n{ANSI_BOLD}MCP System Health Check{ANSI_RESET}")
    print(f"  URL:         {base_url}")
    print(f"  Version:     {version}")
    print(f"  Environment: {env_name}")
    print(f"  Overall:     {_status_icon(overall)} {_color(overall.upper(), ANSI_BOLD)}")
    print()

    if checks:
        print(f"  {'Component':<20} {'Status':<12} Message")
        print(f"  {'-'*20} {'-'*12} {'-'*30}")
        for component, info in checks.items():
            status  = info.get("status", "unknown")
            message = info.get("message", "")
            icon    = _status_icon(status)
            print(f"  {component:<20} {icon} {status:<10} {message}")
    else:
        print("  (no subsystem details returned)")

    print()

    if overall == "healthy":
        print(_color("  All systems operational.", ANSI_GREEN))
        return 0
    if overall == "degraded":
        print(_color("  System degraded — some subsystems need attention.", ANSI_YELLOW))
        return 1
    print(_color("  System unhealthy!", ANSI_RED))
    return 2


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP System health check CLI")
    parser.add_argument("--url",     default="http://localhost:8000", help="API base URL")
    parser.add_argument("--api-key", default=None,  help="X-API-Key for authenticated endpoints")
    parser.add_argument("--timeout", type=int, default=10, help="Request timeout in seconds")
    parser.add_argument("--json",    action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    try:
        data = check_health(args.url, args.api_key, args.timeout)
    except httpx.ConnectError:
        print(_color(f"✗ Cannot reach {args.url}", ANSI_RED), file=sys.stderr)
        sys.exit(3)
    except httpx.HTTPStatusError as exc:
        print(_color(f"✗ HTTP {exc.response.status_code}: {exc.request.url}", ANSI_RED), file=sys.stderr)
        sys.exit(3)
    except Exception as exc:
        print(_color(f"✗ Unexpected error: {exc}", ANSI_RED), file=sys.stderr)
        sys.exit(3)

    if args.json:
        print(json.dumps(data, indent=2))
        sys.exit(0)

    exit_code = print_human(data, args.url)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
