"""Fast local context for changed code.

This module records facts (symbols, tests, imports, and file relationships).
It never decides which changes belong in a commit; the planner model does that.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

from .models import ChangeGraph, ChangeLink, ChangeUnit, WorktreeSnapshot

_LANGUAGES = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".swift": "swift", ".rb": "ruby",
    ".php": "php", ".cs": "csharp", ".c": "c", ".h": "c", ".cpp": "cpp",
    ".cc": "cpp", ".vue": "vue", ".svelte": "svelte", ".sql": "sql",
    ".md": "markdown", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json",
}

_SYMBOL_HEADER = re.compile(
    r"\b(?:def|class|function|func|fn|interface|type|struct|enum)\s+([A-Za-z_$][\w$]*)"
)
_IMPORT = re.compile(
    r"(?:from\s+([\w.]+)\s+import|import\s+([\w./-]+)|require\(['\"]([^'\"]+)|from\s+['\"]([^'\"]+))"
)


def _language(path: str) -> str:
    return _LANGUAGES.get(Path(path).suffix.lower(), "text")


def _kind(path: str) -> str:
    low = path.lower()
    name = Path(low).name
    if "/test" in f"/{low}" or name.startswith("test_") or ".test." in name or ".spec." in name:
        return "test"
    if "migration" in low or Path(low).suffix == ".sql":
        return "migration"
    if name in {"package.json", "pyproject.toml", "cargo.toml", "go.mod", "pom.xml"}:
        return "dependency"
    if Path(low).suffix in {".md", ".rst"} or low.startswith("docs/"):
        return "docs"
    if Path(low).suffix in {".json", ".toml", ".yaml", ".yml"}:
        return "config"
    return "code"


def _python_symbols(path: Path) -> list[tuple[int, int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []
    symbols: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            symbols.append((node.lineno, end, node.name))
    return symbols


def _symbol_for(
    language: str, line: int, header: str, python_symbols: list[tuple[int, int, str]],
) -> str:
    if language == "python":
        matches = [item for item in python_symbols if item[0] <= line <= item[1]]
        if matches:
            start, end, name = min(matches, key=lambda item: item[1] - item[0])
            return name
    match = _SYMBOL_HEADER.search(header)
    return match.group(1) if match else ""


def _imports(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:1_000_000]
    except OSError:
        return set()
    found: set[str] = set()
    for match in _IMPORT.finditer(text):
        value = next((part for part in match.groups() if part), "")
        if value:
            found.add(value.replace("/", ".").strip("."))
    return found


def build_change_graph(snapshot: WorktreeSnapshot) -> ChangeGraph:
    """Build a compact, deterministic graph from the frozen worktree."""
    units: list[ChangeUnit] = []
    root = snapshot.repo_root
    symbols_by_path: dict[str, list[tuple[int, int, str]]] = {}
    for file_change in snapshot.files:
        if not file_change.safety.safe:
            continue
        language = _language(file_change.path)
        path = root / file_change.path
        symbols = symbols_by_path.setdefault(
            file_change.path, _python_symbols(path) if language == "python" else []
        )
        for hunk in file_change.hunks:
            units.append(
                ChangeUnit(
                    unit_id=hunk.hunk_id,
                    hunk_id=hunk.hunk_id,
                    file_path=file_change.path,
                    status=file_change.status,
                    language=language,
                    symbol=_symbol_for(language, max(hunk.new_start, 1), hunk.header, symbols),
                    change_kind=_kind(file_change.path),
                    added=hunk.added[:12],
                    removed=hunk.removed[:12],
                )
            )

    links: list[ChangeLink] = []
    seen: set[tuple[str, str, str]] = set()

    def link(left: ChangeUnit, right: ChangeUnit, kind: str, reason: str) -> None:
        if left.unit_id == right.unit_id:
            return
        source, target = sorted((left.unit_id, right.unit_id))
        key = (source, target, kind)
        if key not in seen:
            seen.add(key)
            links.append(ChangeLink(source=source, target=target, kind=kind, reason=reason))

    by_file: dict[str, list[ChangeUnit]] = defaultdict(list)
    by_symbol: dict[str, list[ChangeUnit]] = defaultdict(list)
    by_stem: dict[str, list[ChangeUnit]] = defaultdict(list)
    for unit in units:
        by_file[unit.file_path].append(unit)
        by_stem[Path(unit.file_path).stem.removeprefix("test_")].append(unit)
        if unit.symbol:
            by_symbol[unit.symbol].append(unit)

    for file_units in by_file.values():
        for left, right in zip(file_units, file_units[1:], strict=False):
            link(left, right, "same_file", "changed regions share a file")
    for symbol, symbol_units in by_symbol.items():
        for left, right in zip(symbol_units, symbol_units[1:], strict=False):
            link(left, right, "same_symbol", f"both regions change {symbol}")
    for stem_units in by_stem.values():
        tests = [unit for unit in stem_units if unit.change_kind == "test"]
        code = [unit for unit in stem_units if unit.change_kind == "code"]
        if tests and code:
            # Same-file links already connect the other hunks. One bridge keeps
            # the relationship without creating every test/code hunk pair.
            link(tests[0], code[0], "test", "test and source file names match")

    imports_by_file = {path: _imports(root / path) for path in by_file}
    module_by_file = {
        path: path.removesuffix(Path(path).suffix).replace("/", ".") for path in by_file
    }
    for source_path, imports in imports_by_file.items():
        for target_path, module in module_by_file.items():
            if source_path == target_path:
                continue
            if any(module.endswith(name) or name.endswith(module) for name in imports):
                # One file-to-file bridge is enough because hunks in each file
                # are already connected. A Cartesian product can make prompts
                # grow quadratically on large changes.
                link(
                    by_file[source_path][0], by_file[target_path][0], "import",
                    f"{source_path} imports {module}",
                )

    return ChangeGraph(units=units, links=links)


def connected_groups(graph: ChangeGraph) -> list[list[str]]:
    """Return local relationship groups for prompt packing, never commit grouping."""
    neighbors: dict[str, set[str]] = {unit.unit_id: set() for unit in graph.units}
    for edge in graph.links:
        neighbors.setdefault(edge.source, set()).add(edge.target)
        neighbors.setdefault(edge.target, set()).add(edge.source)
    groups: list[list[str]] = []
    remaining = set(neighbors)
    while remaining:
        start = min(remaining)
        stack = [start]
        group: list[str] = []
        remaining.remove(start)
        while stack:
            current = stack.pop()
            group.append(current)
            for neighbor in sorted(neighbors[current] & remaining):
                remaining.remove(neighbor)
                stack.append(neighbor)
        groups.append(group)
    return groups
