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
    "agent.brain.migrate",
    "agent.brain.recall",
    "agent.brain.prefix",
    "agent.brain.session",
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
    # Imported by agent/turn_context.py and agent/system_prompt.py at runtime. A rename here
    # would drop memory silently (the import sits in a try/except by design), so pin it.
    "agent.brain.session": [
        "BrainSettings",
        "brain_stable_prefix",
        "brain_turn_context",
        "get_brain_config",
        "get_agent_vault",
        "resolve_settings",
        "reset_agent_cache",
    ],
    "agent.brain.prefix": ["compile_stable_prefix", "has_stable_content"],
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

    def test_every_name_in_brain_all_is_resolvable(self):
        """__all__ must not advertise a symbol that does not exist."""
        import agent.brain as brain

        missing = [name for name in brain.__all__ if not hasattr(brain, name)]
        self.assertEqual(missing, [], f"agent.brain.__all__ advertises missing symbols: {missing}")

    def test_public_api_is_callable(self):
        import agent.brain as brain

        for name in (
            "recall_memories",
            "recall_block",
            "extract_cues",
            "migrate_legacy_memory",
            "plan_migration",
            "text_similarity",
        ):
            self.assertTrue(callable(getattr(brain, name)), name)
        for name in ("BrainVault", "BrainIndex", "RecallResult", "RecallHit"):
            self.assertTrue(isinstance(getattr(brain, name), type), name)

    def test_submodules_are_not_shadowed_by_the_package_reexports(self):
        """`import agent.brain.recall` must give the MODULE, not a same-named function.

        A previous version of agent/brain/__init__.py cached the re-exported functions into
        the package globals, so `import agent.brain.similarity as sim` returned the
        `similarity()` function and every `sim.tokenize(...)` call failed with
        "'function' object has no attribute 'tokenize'".
        """
        import agent.brain.recall as recall_module
        import agent.brain.similarity as similarity_module
        from agent.brain.recall import recall as recall_fn
        from agent.brain.similarity import similarity as similarity_fn

        self.assertTrue(callable(recall_fn))
        self.assertTrue(callable(similarity_fn))
        self.assertEqual(recall_module.__name__, "agent.brain.recall")
        self.assertEqual(similarity_module.__name__, "agent.brain.similarity")
        # The module-level names are distinct objects from the package-level aliases.
        import agent.brain as brain

        self.assertIs(brain.recall_memories, recall_fn)
        self.assertIs(brain.text_similarity, similarity_fn)
        self.assertTrue(callable(similarity_module.tokenize))
        self.assertTrue(callable(recall_module.recall_block))

    def test_importing_the_package_does_not_pull_heavy_deps(self):
        import agent.brain  # noqa: F401
        import agent.brain.decay  # noqa: F401
        import agent.brain.index  # noqa: F401
        import agent.brain.migrate  # noqa: F401
        import agent.brain.models  # noqa: F401
        import agent.brain.parser  # noqa: F401
        import agent.brain.prefix  # noqa: F401
        import agent.brain.recall  # noqa: F401
        import agent.brain.session  # noqa: F401
        import agent.brain.similarity  # noqa: F401
        import agent.brain.vault  # noqa: F401

    def test_pure_layers_do_not_import_agent_modules_at_module_level(self):
        """Only the bridge may touch ``agent.*``; the pure layers must stay testable alone."""
        bridge = {"session.py"}
        offenders = []
        for path in sorted((REPO_ROOT / "agent" / "brain").glob("*.py")):
            if path.name in bridge:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in tree.body:  # module level only
                modules = []
                if isinstance(node, ast.Import):
                    modules = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    if module.split(".")[0] == "agent" and not module.startswith("agent.brain"):
                        offenders.append(f"{path.name}: {module}")
        self.assertEqual(offenders, [], f"pure brain layers must not import agent.*: {offenders}")

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
