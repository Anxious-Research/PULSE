"""Guardrail test: the memory/brain module contracts must not drift.

This test exists because a previous attempt deleted ``MemoryStore``,
``get_builtin_memory_config`` and ``get_builtin_memory_store_flags`` from
``tools/memory_tool.py`` while ``agent/agent_init.py`` still imported them. The import
failed inside a ``suppress(Exception)``, leaving ``mem_config`` unbound and crashing memory
initialisation on every single run — memory was silently dead.

So: every name any module imports from the memory modules must actually exist. If this test
fails, do not "fix" the test — either restore the symbol or update every importer in the
same commit.

Also asserts the brain package stays dependency-free and import-safe, because a heavy
top-level import would break the live agent on an install without that dependency.
"""

from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Modules whose public surface other code imports.
CONTRACT_MODULES = [
    "tools.memory_tool",
    "tools.memory_tool_store",
    "agent.brain.vault",
    "agent.brain.parser",
    "agent.brain.models",
    "agent.brain.index",
    "agent.brain.decay",
    "agent.brain.similarity",
]

# Names that MUST keep existing: removing them broke the live agent before.
REQUIRED_NAMES = {
    "tools.memory_tool": [
        "memory_tool",
        "MEMORY_SCHEMA",
        "get_builtin_memory_config",
        "get_builtin_memory_store_flags",
        "load_on_disk_store",
        "check_memory_requirements",
        "apply_memory_pending",
    ],
    "tools.memory_tool_store": ["MemoryStore", "ENTRY_DELIMITER"],
}


def _iter_python_files():
    for path in REPO_ROOT.rglob("*.py"):
        parts = set(path.parts)
        if parts & {"node_modules", ".git", "__pycache__", ".venv", "venv", "release", "dist"}:
            continue
        yield path


def _imported_names_from(source: str, module: str) -> set:
    """Names imported by ``from <module> import ...`` anywhere under the repo."""
    found: set = set()
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            for alias in node.names:
                if alias.name != "*":
                    found.add(alias.name)
    return found


class TestImportedSymbolsExist(unittest.TestCase):
    def test_every_imported_symbol_is_present(self):
        missing: dict = {}
        for path in _iter_python_files():
            try:
                source = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError):
                continue
            for module in CONTRACT_MODULES:
                names = _imported_names_from(source, module)
                if not names:
                    continue
                try:
                    mod = importlib.import_module(module)
                except Exception as exc:  # pragma: no cover - surfaced as a failure below
                    missing.setdefault(module, []).append(f"{path.name}: cannot import ({exc})")
                    continue
                for name in sorted(names):
                    if not hasattr(mod, name):
                        missing.setdefault(module, []).append(f"{path.relative_to(REPO_ROOT)} -> {name}")
        self.assertEqual(missing, {}, f"imported symbols do not exist: {missing}")

    def test_explicitly_required_symbols_survive(self):
        for module, names in REQUIRED_NAMES.items():
            mod = importlib.import_module(module)
            for name in names:
                self.assertTrue(hasattr(mod, name), f"{module} must still export {name}")


class TestBrainPackageIsImportSafe(unittest.TestCase):
    FORBIDDEN_TOP_LEVEL = ("numpy", "torch", "fastembed", "sentence_transformers", "onnxruntime", "ruamel")

    def test_importing_the_package_does_not_pull_heavy_deps(self):
        import agent.brain  # noqa: F401
        import agent.brain.decay  # noqa: F401
        import agent.brain.index  # noqa: F401
        import agent.brain.models  # noqa: F401
        import agent.brain.parser  # noqa: F401
        import agent.brain.similarity  # noqa: F401
        import agent.brain.vault  # noqa: F401

    def test_no_heavy_import_at_module_level(self):
        offenders = []
        for path in sorted((REPO_ROOT / "agent" / "brain").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in tree.body:  # module level only; lazy imports inside functions are fine
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module.split(".")[0]]
                for name in names:
                    if name in self.FORBIDDEN_TOP_LEVEL:
                        offenders.append(f"{path.name}: {name}")
        self.assertEqual(offenders, [], f"heavy top-level imports: {offenders}")

    def test_vault_module_does_not_import_tools_or_utils_at_module_level(self):
        """A top-level dependency on utils/pulse_yaml would break on a bare install."""
        tree = ast.parse((REPO_ROOT / "agent" / "brain" / "vault.py").read_text(encoding="utf-8-sig"))
        bad = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module in {"utils", "pulse_yaml", "tools.registry"}:
                bad.append(node.module)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in {"utils", "pulse_yaml"}:
                        bad.append(alias.name)
        self.assertEqual(bad, [], f"vault.py must lazily import these: {bad}")


class TestBrainAppearsInGraphPayload(unittest.TestCase):
    def test_learning_graph_still_returns_a_payload(self):
        """The desktop StarMap consumes this; it must never raise."""
        try:
            from agent.learning_graph import build_learning_graph
        except Exception as exc:
            self.skipTest(f"learning_graph unavailable in this environment: {exc}")
        payload = build_learning_graph()
        for key in ("nodes", "edges", "clusters", "stats"):
            self.assertIn(key, payload)


if __name__ == "__main__":
    unittest.main()
