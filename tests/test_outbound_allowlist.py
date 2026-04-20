"""FR41: enforce allowlist for direct :mod:`httpx` imports under ``sentinel_prism`` (Story 5.5)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from sentinel_prism.compliance.outbound_allowlist import ALLOWED_HTTPX_SOURCE_FILES

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "src" / "sentinel_prism"
_OUTBOUND_DOC_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "regulatory-outbound-allowlist.md"
)
_HTTPX_CALL_NAMES = frozenset({"AsyncClient", "Client", "post", "request"})


def _py_files_under_package() -> list[Path]:
    return sorted(p for p in _PACKAGE_ROOT.rglob("*.py") if p.is_file())


def _relative_posix(path: Path) -> str:
    return path.relative_to(_PACKAGE_ROOT).as_posix()


def _collect_httpx_imports(tree: ast.AST) -> tuple[set[str], set[str]]:
    module_aliases: set[str] = set()
    imported_symbols: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "httpx" or alias.name.startswith("httpx."):
                    # import httpx._client binds `httpx` unless explicit alias is provided.
                    alias_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                    module_aliases.add(alias_name)
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "httpx" or node.module.startswith("httpx."))
        ):
            for alias in node.names:
                imported_symbols.add(alias.asname or alias.name)
    return module_aliases, imported_symbols


def _uses_httpx_calls(
    tree: ast.AST, module_aliases: set[str], imported_symbols: set[str]
) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if isinstance(node.func, ast.Name):
            if node.func.id in imported_symbols and node.func.id in _HTTPX_CALL_NAMES:
                return True
            continue

        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            owner = node.func.value.id
            if owner in module_aliases and node.func.attr in _HTTPX_CALL_NAMES:
                return True
            if owner in imported_symbols and node.func.attr in _HTTPX_CALL_NAMES:
                return True
    return False


def _doc_allowlist_entries() -> set[str]:
    text = _OUTBOUND_DOC_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)^## Machine-checked direct `httpx` allowlist\s*\n(.*?)(?:\n## |\Z)",
        text,
    )
    assert match, (
        f"{_OUTBOUND_DOC_PATH}: missing 'Machine-checked direct `httpx` allowlist' section"
    )

    entries: set[str] = set()
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        path_match = re.match(r"- `src/sentinel_prism/(.+)`$", line)
        if path_match:
            entries.add(path_match.group(1))

    assert entries, f"{_OUTBOUND_DOC_PATH}: no allowlist entries were parsed from the section"
    return entries


def test_only_allowlisted_files_import_httpx() -> None:
    offenders: list[str] = []
    direct_httpx_users: set[str] = set()

    for path in _py_files_under_package():
        rel = _relative_posix(path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            offenders.append(f"{rel}: file is not UTF-8 decodable")
            continue
        tree = ast.parse(text, filename=str(path))
        module_aliases, imported_symbols = _collect_httpx_imports(tree)
        has_httpx_import = bool(module_aliases or imported_symbols)
        has_httpx_call = _uses_httpx_calls(tree, module_aliases, imported_symbols)

        if not has_httpx_import and not has_httpx_call:
            continue
        direct_httpx_users.add(rel)

        if rel not in ALLOWED_HTTPX_SOURCE_FILES:
            offenders.append(
                f"{rel}: contains direct httpx import/call patterns but is not in "
                "ALLOWED_HTTPX_SOURCE_FILES — "
                "update src/sentinel_prism/compliance/outbound_allowlist.py and "
                "docs/regulatory-outbound-allowlist.md"
            )

    assert not offenders, "Disallowed httpx usage:\n" + "\n".join(offenders)

    stale = sorted(ALLOWED_HTTPX_SOURCE_FILES - direct_httpx_users)
    assert not stale, (
        "Allowlist entries are not using direct httpx import/call patterns (remove stale paths "
        "or restore usage): "
        + ", ".join(stale)
    )

    missing_files = sorted(
        rel for rel in ALLOWED_HTTPX_SOURCE_FILES if not (_PACKAGE_ROOT / rel).is_file()
    )
    assert not missing_files, "Allowlist references missing files: " + ", ".join(missing_files)


def test_allowlist_constant_matches_module_export() -> None:
    """Guard against accidental empty allowlist."""
    assert len(ALLOWED_HTTPX_SOURCE_FILES) >= 1


def test_inventory_doc_stays_in_lockstep_with_python_allowlist() -> None:
    """Keep human-readable inventory and CI allowlist synchronized."""
    doc_entries = _doc_allowlist_entries()

    assert doc_entries == ALLOWED_HTTPX_SOURCE_FILES, (
        "Allowlist drift between docs/regulatory-outbound-allowlist.md and "
        "src/sentinel_prism/compliance/outbound_allowlist.py\n"
        f"Only in docs: {sorted(doc_entries - ALLOWED_HTTPX_SOURCE_FILES)}\n"
        f"Only in python allowlist: {sorted(ALLOWED_HTTPX_SOURCE_FILES - doc_entries)}"
    )
