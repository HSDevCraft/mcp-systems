"""Module Registry — catalogs, validates, versions, and dispatches module calls."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
from pathlib import Path
from typing import Any

from packaging.version import Version  # type: ignore[import]

from src.utils.exceptions import ModuleLoadError, ModuleNotFoundError
from src.utils.logger import get_logger
from src.utils.metrics import get_metrics

logger = get_logger(__name__, component="module_registry")
metrics = get_metrics()


class ModuleRegistry:
    """Central catalog for all MCP modules.

    Supports both static registration (import-time) and dynamic discovery
    (filesystem scan at startup or runtime).

    Thread/async safety: Uses asyncio.Lock for mutations. Reads are lock-free
    since Python's dict reads are GIL-safe and we never mutate during reads.
    """

    def __init__(self) -> None:
        # Indexed as {name: {version_str: module_instance}}
        self._modules: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    # ── Registration ──────────────────────────────────────────────────────────

    async def register(self, module: Any) -> None:
        """Register a module instance.

        Calls module.on_load() after registration. If on_load() raises,
        the module is NOT registered and a ModuleLoadError is raised.

        Args:
            module: An instance of a class implementing MCPModule interface.

        Raises:
            ModuleLoadError: If on_load() raises an exception.
            ValueError: If name or version is missing/invalid.
        """
        name: str = getattr(module, "name", None)  # type: ignore[assignment]
        version: str = getattr(module, "version", "1.0.0")

        if not name:
            raise ValueError(f"Module {module.__class__.__name__} must define 'name'")

        try:
            await module.on_load()
        except Exception as exc:
            raise ModuleLoadError(name, str(exc)) from exc

        async with self._lock:
            if name not in self._modules:
                self._modules[name] = {}
            self._modules[name][version] = module

        metrics.set_registered_modules(self._total_count())
        logger.info(
            "module_registered",
            module_name=name,
            module_version=version,
            module_class=module.__class__.__name__,
        )

    async def unregister(self, name: str, version: str | None = None) -> None:
        """Unregister a module (or a specific version).

        Calls module.on_unload() before removing from registry.

        Args:
            name: Module name.
            version: Specific version to remove; if None, removes all versions.
        """
        async with self._lock:
            if name not in self._modules:
                return
            if version is not None:
                module = self._modules[name].pop(version, None)
                if module:
                    await module.on_unload()
                if not self._modules[name]:
                    del self._modules[name]
            else:
                for ver, module in self._modules[name].items():
                    await module.on_unload()
                del self._modules[name]

        metrics.set_registered_modules(self._total_count())
        logger.info("module_unregistered", module_name=name, module_version=version)

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get(self, name: str, version: str | None = None) -> Any:
        """Retrieve a module instance by name (and optional version).

        Args:
            name: Module name.
            version: Specific version string; if None, returns latest.

        Returns:
            Module instance.

        Raises:
            ModuleNotFoundError: If module is not registered.
        """
        if name not in self._modules:
            raise ModuleNotFoundError(name, version)

        versions = self._modules[name]
        if not versions:
            raise ModuleNotFoundError(name, version)

        if version is not None:
            if version not in versions:
                raise ModuleNotFoundError(name, version)
            return versions[version]

        # Return latest version
        latest = max(versions.keys(), key=lambda v: Version(v))
        return versions[latest]

    def list_modules(self) -> list[dict[str, Any]]:
        """Return a summary of all registered modules.

        Returns:
            List of dicts with name, version, description, tags, schemas.
        """
        result = []
        for name, versions in self._modules.items():
            for version, module in versions.items():
                result.append(
                    {
                        "name": name,
                        "version": version,
                        "description": getattr(module, "description", ""),
                        "tags": getattr(module, "tags", []),
                        "input_schema": (
                            module.input_schema.model_json_schema()
                            if hasattr(module, "input_schema")
                            else {}
                        ),
                        "output_schema": (
                            module.output_schema.model_json_schema()
                            if hasattr(module, "output_schema")
                            else {}
                        ),
                    }
                )
        return result

    def is_registered(self, name: str, version: str | None = None) -> bool:
        """Check if a module is registered."""
        if name not in self._modules:
            return False
        if version is not None:
            return version in self._modules[name]
        return bool(self._modules[name])

    # ── Execution ─────────────────────────────────────────────────────────────

    async def execute(
        self,
        name: str,
        input_data: Any,
        execution_context: Any,
        version: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Execute a module by name.

        Applies lifecycle hooks (before_execute, after_execute, on_error)
        and enforces a timeout if specified.

        Args:
            name: Module name.
            input_data: Validated input (Pydantic model or dict).
            execution_context: ExecutionContext passed to the module.
            version: Optional version pin.
            timeout: Max execution time in seconds.

        Returns:
            Module output (Pydantic model).

        Raises:
            ModuleNotFoundError: If module is not registered.
            ModuleTimeoutError: If execution exceeds timeout.
            ModuleExecutionError: If module raises an unexpected error.
        """
        from src.utils.exceptions import ModuleExecutionError, ModuleTimeoutError

        module = self.get(name, version)
        mod_version: str = getattr(module, "version", "1.0.0")

        # Validate and coerce input
        if hasattr(module, "input_schema") and isinstance(input_data, dict):
            input_data = module.input_schema(**input_data)

        # Before hook
        if hasattr(module, "before_execute"):
            await module.before_execute(input_data, execution_context)

        import time

        start = time.perf_counter()
        status = "success"
        output = None

        try:
            coro = module.execute(input_data, execution_context)
            if timeout is not None:
                try:
                    output = await asyncio.wait_for(coro, timeout=timeout)
                except asyncio.TimeoutError:
                    raise ModuleTimeoutError(name, timeout)
            else:
                output = await coro

        except (ModuleTimeoutError,) as exc:
            status = "timeout"
            if hasattr(module, "on_error"):
                await module.on_error(exc, execution_context)
            raise

        except Exception as exc:
            status = "error"
            if hasattr(module, "on_error"):
                await module.on_error(exc, execution_context)
            raise ModuleExecutionError(name, str(exc)) from exc

        finally:
            latency = time.perf_counter() - start
            metrics.record_module_execution(name, mod_version, status, latency)

        # After hook
        if hasattr(module, "after_execute"):
            await module.after_execute(output, execution_context)

        return output

    # ── Health ─────────────────────────────────────────────────────────────────

    async def health_check_all(self) -> dict[str, Any]:
        """Run health_check() on all registered modules.

        Returns:
            Dict mapping module names to health status dicts.
        """
        results: dict[str, Any] = {}
        tasks = []
        keys = []
        for name, versions in self._modules.items():
            latest = max(versions.keys(), key=lambda v: Version(v))
            module = versions[latest]
            keys.append(f"{name}@{latest}")
            tasks.append(module.health_check())

        statuses = await asyncio.gather(*tasks, return_exceptions=True)
        for key, status in zip(keys, statuses, strict=False):
            name_part = key.split("@")[0]
            ver_part = key.split("@")[1]
            if isinstance(status, Exception):
                results[key] = {"healthy": False, "message": str(status)}
                metrics.set_module_health(name_part, ver_part, False)
            else:
                results[key] = status.model_dump() if hasattr(status, "model_dump") else status
                healthy = results[key].get("healthy", False)
                metrics.set_module_health(name_part, ver_part, healthy)

        return results

    # ── Dynamic Discovery ─────────────────────────────────────────────────────

    async def discover(self, path: str | Path) -> int:
        """Scan a directory for Python files containing MCPModule subclasses.

        Imports each .py file and registers any concrete MCPModule subclasses found.

        Args:
            path: Directory path to scan.

        Returns:
            Number of modules successfully registered.
        """
        from src.modules.base import MCPModule

        discovered = 0
        scan_path = Path(path)

        if not scan_path.exists():
            logger.warning("module_discovery_path_not_found", path=str(scan_path))
            return 0

        for py_file in scan_path.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    py_file.stem, py_file
                )
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore[union-attr]

                for _, cls in inspect.getmembers(mod, inspect.isclass):
                    if (
                        issubclass(cls, MCPModule)
                        and cls is not MCPModule
                        and not inspect.isabstract(cls)
                    ):
                        try:
                            instance = cls()
                            await self.register(instance)
                            discovered += 1
                        except Exception as exc:
                            logger.warning(
                                "module_discovery_register_failed",
                                class_name=cls.__name__,
                                error=str(exc),
                            )
            except Exception as exc:
                logger.warning(
                    "module_discovery_import_failed",
                    file=str(py_file),
                    error=str(exc),
                )

        logger.info("module_discovery_complete", discovered=discovered, path=str(scan_path))
        return discovered

    # ── Internals ─────────────────────────────────────────────────────────────

    def _total_count(self) -> int:
        return sum(len(versions) for versions in self._modules.values())

    async def shutdown(self) -> None:
        """Gracefully unload all modules."""
        for name in list(self._modules.keys()):
            await self.unregister(name)
        logger.info("module_registry_shutdown")
