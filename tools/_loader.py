"""Tool module loader with hot-reload support.

The ToolLoader discovers, loads, and manages tool modules from the tools/ directory.
It supports hot-reloading of modules when files are modified.

Supports both:
- Single file modules: tools/example.py
- Package modules: tools/example/__init__.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._registry import ToolRegistry


class ToolLoader:
    """Discovers, loads, and hot-reloads tool modules.

    Tool modules are Python files in the tools/ directory that don't start
    with an underscore, OR subdirectories containing an __init__.py file.
    
    Each module must export:
    - MODULE_NAME: str - Unique identifier
    - MODULE_VERSION: str - Version for reload detection
    - TOOLS: list[ToolDef] - Tool definitions

    Optional exports:
    - initialize() -> None - Called after loading
    - cleanup() -> None - Called before unloading
    - SYSTEM_PROMPT: str - Added to system context

    Usage:
        loader = ToolLoader(tools_dir, registry)
        await loader.load_all()
        loader.start_watching()  # Enable hot-reload
    """

    def __init__(self, tools_dir: Path, registry: ToolRegistry) -> None:
        """Initialize the loader.

        Args:
            tools_dir: Path to the tools/ directory
            registry: ToolRegistry instance to register tools with
        """
        self.tools_dir = tools_dir
        self.registry = registry
        self._modules: dict[str, ModuleType] = {}
        self._module_versions: dict[str, str] = {}
        self._module_mtimes: dict[str, float] = {}
        self._module_paths: dict[str, Path] = {}  # Track actual file/dir path
        self._observer: Any = None
        self._watching = False
        self._reload_callbacks: list[callable] = []

    def discover_modules(self) -> list[str]:
        """Find all tool module files and packages.

        Returns:
            List of module names (without .py extension for files)
        """
        modules = []
        
        # Discover single-file modules (tools/*.py)
        for f in self.tools_dir.glob("*.py"):
            # Skip private modules (starting with _)
            if f.name.startswith("_"):
                continue
            # Skip __pycache__ etc
            if f.name.startswith("__"):
                continue
            modules.append(f.stem)
        
        # Discover package modules (tools/*/__init__.py)
        for d in self.tools_dir.iterdir():
            if not d.is_dir():
                continue
            # Skip private/hidden directories
            if d.name.startswith("_") or d.name.startswith("."):
                continue
            # Skip __pycache__
            if d.name == "__pycache__":
                continue
            # Check for __init__.py
            init_file = d / "__init__.py"
            if init_file.exists():
                # Don't add if there's already a .py file with same name
                if d.name not in modules:
                    modules.append(d.name)
        
        return sorted(modules)

    def _get_module_path(self, module_name: str) -> Path | None:
        """Get the path to a module file or package.
        
        Args:
            module_name: Name of the module
            
        Returns:
            Path to .py file or package directory, or None if not found
        """
        # Check for single-file module first
        file_path = self.tools_dir / f"{module_name}.py"
        if file_path.exists():
            return file_path
        
        # Check for package module
        pkg_path = self.tools_dir / module_name
        if pkg_path.is_dir() and (pkg_path / "__init__.py").exists():
            return pkg_path
        
        return None

    def _get_module_mtime(self, module_path: Path) -> float:
        """Get modification time for a module.
        
        For packages, returns the most recent mtime of any .py file in the package.
        
        Args:
            module_path: Path to module file or package directory
            
        Returns:
            Most recent modification timestamp
        """
        if module_path.is_file():
            return module_path.stat().st_mtime
        
        # For packages, check all .py files
        max_mtime = 0.0
        for py_file in module_path.rglob("*.py"):
            mtime = py_file.stat().st_mtime
            if mtime > max_mtime:
                max_mtime = mtime
        return max_mtime

    async def load_module(self, module_name: str) -> bool:
        """Load or reload a single tool module.

        Args:
            module_name: Name of the module to load (without .py)

        Returns:
            True if the module was loaded successfully
        """
        module_path = self._get_module_path(module_name)
        if module_path is None:
            print(f"[tools] Module not found: {module_name}")
            return False

        # Get file modification time
        mtime = self._get_module_mtime(module_path)

        # Check if we need to reload
        if module_name in self._modules:
            old_mtime = self._module_mtimes.get(module_name, 0)
            if mtime <= old_mtime:
                # File hasn't changed
                return True

            # Cleanup old module
            await self._cleanup_module(module_name)

        try:
            # Determine how to load based on file vs package
            is_package = module_path.is_dir()
            
            if is_package:
                # Load package from __init__.py
                init_path = module_path / "__init__.py"
                spec = importlib.util.spec_from_file_location(
                    f"tools.{module_name}",
                    init_path,
                    submodule_search_locations=[str(module_path)]
                )
            else:
                # Load single file module
                spec = importlib.util.spec_from_file_location(
                    f"tools.{module_name}", module_path
                )
            
            if spec is None or spec.loader is None:
                print(f"[tools] Failed to load spec for {module_name}")
                return False

            module = importlib.util.module_from_spec(spec)
            sys.modules[f"tools.{module_name}"] = module
            spec.loader.exec_module(module)

            # Validate module interface
            if not hasattr(module, "TOOLS"):
                print(f"[tools] Module {module_name} missing TOOLS export")
                return False

            # Get module metadata
            mod_name = getattr(module, "MODULE_NAME", module_name)
            mod_version = getattr(module, "MODULE_VERSION", "0.0.0")

            # Initialize if needed
            if hasattr(module, "initialize"):
                init_fn = module.initialize
                if asyncio.iscoroutinefunction(init_fn):
                    await init_fn()
                else:
                    init_fn()

            # Register tools
            tools = module.TOOLS
            for tool_def in tools:
                self.registry.register(tool_def, source_module=mod_name)

            # Register system prompt only if module has active tools
            if tools:
                system_prompt = getattr(module, "SYSTEM_PROMPT", None)
                if system_prompt:
                    self.registry.register_system_prompt(mod_name, system_prompt)

            # Store module reference
            self._modules[module_name] = module
            self._module_versions[module_name] = mod_version
            self._module_mtimes[module_name] = mtime
            self._module_paths[module_name] = module_path

            tool_names = [t.name for t in tools]
            pkg_indicator = " (package)" if is_package else ""
            print(f"[tools] Loaded {mod_name} v{mod_version}{pkg_indicator}: {tool_names}")
            return True

        except Exception as e:
            print(f"[tools] Error loading {module_name}: {e}")
            import traceback

            traceback.print_exc()
            return False

    async def _cleanup_module(self, module_name: str) -> None:
        """Clean up a module before unloading/reloading."""
        module = self._modules.get(module_name)
        if module is None:
            return

        # Call cleanup if defined
        if hasattr(module, "cleanup"):
            cleanup_fn = module.cleanup
            try:
                if asyncio.iscoroutinefunction(cleanup_fn):
                    await cleanup_fn()
                else:
                    cleanup_fn()
            except Exception as e:
                print(f"[tools] Error during cleanup of {module_name}: {e}")

        # Unregister tools and system prompt
        mod_name = getattr(module, "MODULE_NAME", module_name)
        removed = self.registry.unregister_module(mod_name)
        if removed:
            print(f"[tools] Unregistered tools from {mod_name}: {removed}")
        self.registry.unregister_system_prompt(mod_name)

        # Remove from sys.modules (including any submodules for packages)
        keys_to_remove = [k for k in sys.modules if k.startswith(f"tools.{module_name}")]
        for key in keys_to_remove:
            del sys.modules[key]

        # Remove from our tracking
        del self._modules[module_name]
        if module_name in self._module_versions:
            del self._module_versions[module_name]
        if module_name in self._module_mtimes:
            del self._module_mtimes[module_name]
        if module_name in self._module_paths:
            del self._module_paths[module_name]

    async def unload_module(self, module_name: str) -> bool:
        """Unload a tool module.

        Args:
            module_name: Name of the module to unload

        Returns:
            True if the module was unloaded
        """
        if module_name not in self._modules:
            return False

        await self._cleanup_module(module_name)
        return True

    async def load_all(self) -> dict[str, bool]:
        """Load all discovered tool modules.

        Returns:
            Dict mapping module names to load success status
        """
        results = {}
        for name in self.discover_modules():
            results[name] = await self.load_module(name)
        return results

    async def reload_module(self, module_name: str) -> bool:
        """Reload a specific module.

        Args:
            module_name: Name of the module to reload

        Returns:
            True if reload was successful
        """
        # Force reload by clearing mtime
        if module_name in self._module_mtimes:
            self._module_mtimes[module_name] = 0

        success = await self.load_module(module_name)

        # Notify callbacks
        for callback in self._reload_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(module_name, success)
                else:
                    callback(module_name, success)
            except Exception as e:
                print(f"[tools] Reload callback error: {e}")

        return success

    async def reload_all(self) -> dict[str, bool]:
        """Reload all modules.

        Returns:
            Dict mapping module names to reload success status
        """
        # Clear all mtimes to force reload
        self._module_mtimes.clear()
        return await self.load_all()

    def on_reload(self, callback: callable) -> None:
        """Register a callback to be called when modules are reloaded.

        Args:
            callback: Function(module_name: str, success: bool) -> None
        """
        self._reload_callbacks.append(callback)

    def _get_module_for_path(self, file_path: Path) -> str | None:
        """Find which module a file path belongs to.
        
        Args:
            file_path: Path to a .py file
            
        Returns:
            Module name, or None if not part of any module
        """
        # Check if it's a direct module file
        if file_path.parent == self.tools_dir:
            if file_path.suffix == ".py" and not file_path.name.startswith("_"):
                return file_path.stem
            return None
        
        # Check if it's inside a package
        # Walk up to find if parent is a package in tools/
        current = file_path.parent
        while current != self.tools_dir and current != current.parent:
            if current.parent == self.tools_dir:
                # current is a direct subdirectory of tools/
                if (current / "__init__.py").exists():
                    return current.name
            current = current.parent
        
        return None

    def start_watching(self) -> bool:
        """Start watching for file changes (hot-reload).

        Returns:
            True if watching was started successfully
        """
        if self._watching:
            return True

        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            print("[tools] watchdog not installed, hot-reload disabled")
            print("[tools] Install with: pip install watchdog")
            return False

        class ToolFileHandler(FileSystemEventHandler):
            def __init__(handler_self, loader: ToolLoader):
                handler_self.loader = loader
                handler_self._debounce: dict[str, float] = {}
                handler_self._debounce_delay = 0.5  # seconds

            def _handle_change(handler_self, event):
                if event.is_directory:
                    return

                path = Path(event.src_path)
                if not path.suffix == ".py":
                    return
                if path.name.startswith("_") and path.name != "__init__.py":
                    return

                # Find which module this file belongs to
                module_name = handler_self.loader._get_module_for_path(path)
                if module_name is None:
                    return

                now = time.time()

                # Debounce rapid changes
                last_change = handler_self._debounce.get(module_name, 0)
                if now - last_change < handler_self._debounce_delay:
                    return
                handler_self._debounce[module_name] = now

                print(f"[tools] Detected change in {module_name}, reloading...")
                asyncio.create_task(handler_self.loader.reload_module(module_name))

            def on_modified(handler_self, event):
                handler_self._handle_change(event)

            def on_created(handler_self, event):
                # Treat new files same as modifications
                handler_self._handle_change(event)

        handler = ToolFileHandler(self)
        self._observer = Observer()
        # Watch recursively to catch package changes
        self._observer.schedule(handler, str(self.tools_dir), recursive=True)
        self._observer.start()
        self._watching = True
        print(f"[tools] Watching {self.tools_dir} for changes (including packages)")
        return True

    def stop_watching(self) -> None:
        """Stop watching for file changes."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        self._watching = False
        print("[tools] Stopped watching for changes")

    def is_watching(self) -> bool:
        """Check if hot-reload watching is active."""
        return self._watching

    def get_loaded_modules(self) -> dict[str, str]:
        """Get dict of loaded module names to versions."""
        return {
            name: self._module_versions.get(name, "unknown")
            for name in self._modules.keys()
        }

    async def shutdown(self) -> None:
        """Shutdown the loader, cleaning up all modules."""
        self.stop_watching()

        # Cleanup all modules
        for module_name in list(self._modules.keys()):
            await self._cleanup_module(module_name)
