#!/usr/bin/env python3
"""Validate a module file and print its schema without registering it.

Useful for testing a new module implementation before adding it to the
plugin directory.

Usage:
    python scripts/register_module.py src/modules/plugins/echo.py
    python scripts/register_module.py my_module.py --run --input '{"text":"hello"}'
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import json
import sys
from pathlib import Path


async def validate_and_print(module_path: str, run: bool, raw_input: str | None) -> int:
    path = Path(module_path)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        print(f"Cannot load: {path}", file=sys.stderr)
        return 1

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    # Find MCPModule subclasses
    try:
        from src.modules.base import MCPModule
    except ImportError:
        print("Cannot import MCPModule. Run from repo root with PYTHONPATH set.", file=sys.stderr)
        return 1

    found = [
        cls for _, cls in inspect.getmembers(mod, inspect.isclass)
        if issubclass(cls, MCPModule) and cls is not MCPModule and not inspect.isabstract(cls)
    ]

    if not found:
        print(f"No concrete MCPModule subclasses found in {path}", file=sys.stderr)
        return 1

    for cls in found:
        instance = cls()
        print(f"\n{'='*60}")
        print(f"  Module:      {instance.name}")
        print(f"  Version:     {instance.version}")
        print(f"  Description: {instance.description}")
        print(f"  Tags:        {', '.join(getattr(instance, 'tags', []))}")

        if hasattr(instance, "input_schema"):
            print(f"\n  Input Schema:")
            print(f"  {json.dumps(instance.input_schema.model_json_schema(), indent=4)}")
        if hasattr(instance, "output_schema"):
            print(f"\n  Output Schema:")
            print(f"  {json.dumps(instance.output_schema.model_json_schema(), indent=4)}")

        # on_load validation
        print(f"\n  Running on_load()... ", end="")
        try:
            await instance.on_load()
            print("OK")
        except Exception as exc:
            print(f"FAILED: {exc}", file=sys.stderr)
            return 1

        # Health check
        print(f"  Running health_check()... ", end="")
        status = await instance.health_check()
        if status.healthy:
            print(f"OK ({status.message})")
        else:
            print(f"UNHEALTHY: {status.message}")

        # Optional test execution
        if run and raw_input:
            print(f"\n  Running execute() with input: {raw_input}")
            input_dict = json.loads(raw_input)
            from src.core.orchestrator import ExecutionContext
            ctx = ExecutionContext()
            try:
                validated_input = instance.input_schema(**input_dict)
                output = await instance.execute(validated_input, ctx)
                print(f"  Output: {json.dumps(output.model_dump(), indent=4)}")
            except Exception as exc:
                print(f"  Execute FAILED: {exc}", file=sys.stderr)
                return 1

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an MCPModule implementation")
    parser.add_argument("module_file", help="Path to the Python module file")
    parser.add_argument("--run", action="store_true", help="Also run execute()")
    parser.add_argument("--input", default=None, help='JSON input for execute(), e.g. \'{"text":"hi"}\'')
    args = parser.parse_args()

    # Add repo root to sys.path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    exit_code = asyncio.run(validate_and_print(args.module_file, args.run, args.input))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
