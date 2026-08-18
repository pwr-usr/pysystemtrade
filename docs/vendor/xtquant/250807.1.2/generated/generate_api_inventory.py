#!/usr/bin/env python3
"""Generate a static XTQuant API inventory from the pinned official wheel.

The wheel is parsed as a ZIP archive and Python sources are parsed with ``ast``.
Nothing from the wheel is imported or executed.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import hashlib
import re
import sys
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


VERSION = "250807.1.2"
EXPECTED_SHA256 = "91f19ff9a92971c5abe64fbd077e5212e0418f0820aa3427aef3444230f72921"
PACKAGE_PATHS = {
    "xtdata": "xtquant/xtdata.py",
    "xttrader": "xtquant/xttrader.py",
    "xtdatacenter": "xtquant/xtdatacenter.py",
    "xtconstant": "xtquant/xtconstant.py",
    "xttype": "xtquant/xttype.py",
}
MANUAL_PATHS = {
    "xtdata.md": "xtquant/doc/xtdata.md",
    "xttrader.md": "xtquant/doc/xttrader.md",
}


@dataclass(frozen=True)
class SourceSymbol:
    module: str
    qualname: str
    leaf: str
    kind: str
    line: int
    args: str | None = None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_wheel(wheel: Path) -> tuple[dict[str, str], dict[str, str]]:
    actual_hash = sha256(wheel)
    if actual_hash != EXPECTED_SHA256:
        raise ValueError(
            f"wheel SHA-256 mismatch: expected {EXPECTED_SHA256}, got {actual_hash}"
        )

    with zipfile.ZipFile(wheel) as archive:
        source = {
            module: archive.read(path).decode("utf-8-sig")
            for module, path in PACKAGE_PATHS.items()
        }
        manuals = {
            name: archive.read(path).decode("utf-8-sig")
            for name, path in MANUAL_PATHS.items()
        }
    return source, manuals


def public(name: str) -> bool:
    return not name.startswith("_")


def one_line(text: str) -> str:
    return re.sub(r"\s*\r?\n\s*", " ", text).strip()


def markdown_code(text: str) -> str:
    return f"`{text.replace('|', '&#124;')}`"


def function_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return one_line(ast.unparse(node.args))


def function_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef, qualified_name: str | None = None
) -> str:
    name = qualified_name or node.name
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    result = f"{prefix}{name}({function_args(node)})"
    if node.returns is not None:
        result += f" -> {one_line(ast.unparse(node.returns))}"
    return result


def direct_functions(
    body: Iterable[ast.stmt], include_constructor: bool = False
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    functions = []
    for node in body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if public(node.name) or (
            include_constructor and node.name in {"__init__", "__new__"}
        ):
            functions.append(node)
    return functions


def direct_classes(tree: ast.Module) -> list[ast.ClassDef]:
    return [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and public(node.name)
    ]


def declared_exports(tree: ast.Module) -> set[str] | None:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            continue
        try:
            return set(ast.literal_eval(node.value))
        except (ValueError, TypeError):
            return None
    return None


def assignment_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [target.id for target in targets if isinstance(target, ast.Name)]


def public_aliases(tree: ast.Module) -> list[tuple[str, str, int]]:
    aliases = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if isinstance(value, ast.Name):
            target_value = value.id
        elif isinstance(value, ast.Attribute):
            target_value = one_line(ast.unparse(value))
        else:
            continue
        for name in assignment_names(node):
            if public(name):
                aliases.append((name, target_value, node.lineno))
    return aliases


def public_literal_values(tree: ast.Module) -> list[tuple[str, str, int]]:
    values = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if isinstance(node.value, (ast.Name, ast.Attribute)):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
        for name in assignment_names(node):
            if public(name) and name != "__all__":
                values.append((name, deterministic_repr(value), node.lineno))
    return values


def exact_token_present(text: str, token: str) -> bool:
    return (
        re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", text)
        is not None
    )


def manual_marker(manuals: dict[str, str], token: str) -> str:
    found = [name for name, text in manuals.items() if exact_token_present(text, token)]
    if not found:
        return "—"
    return ", ".join(markdown_code(name) for name in found)


def source_header(module_path: str) -> list[str]:
    return [
        "> **Source-derived inventory.** Generated by statically parsing "
        f"{markdown_code(module_path)} from the official XTQuant {VERSION} wheel; the package is not imported or executed.",
        ">",
        f"> Wheel SHA-256: {markdown_code(EXPECTED_SHA256)}.",
        ">",
        "> Public means a declaration or alias whose name does not begin with an underscore. "
        "This is a syntactic inventory, not a support guarantee. Runtime/native exports, wildcard imports, monkey-patching, overloads, broker entitlements, and behavior implemented only in Windows binaries may not be visible.",
        "",
    ]


def render_callable_module(
    module: str,
    title: str,
    source: str,
    manuals: dict[str, str],
) -> tuple[str, list[SourceSymbol]]:
    tree = ast.parse(source)
    exports = declared_exports(tree)
    functions = direct_functions(tree.body)
    classes = direct_classes(tree)
    aliases = public_aliases(tree)
    literal_values = public_literal_values(tree)
    records: list[SourceSymbol] = []

    lines = [f"# {title}", ""] + source_header(PACKAGE_PATHS[module])
    lines += [
        "Signatures reproduce the pinned Python wrapper. They do not describe the signatures of calls made directly into native `.pyd` modules.",
        "",
        "## Module functions",
        "",
        "| Symbol | Source signature | Line | `__all__` | Bundled-manual token |",
        "|---|---|---:|---|---|",
    ]
    for node in functions:
        signature = function_signature(node)
        export_marker = (
            "n/a" if exports is None else ("yes" if node.name in exports else "no")
        )
        lines.append(
            f"| {markdown_code(node.name)} | {markdown_code(signature)} | {node.lineno} | "
            f"{export_marker} | {manual_marker(manuals, node.name)} |"
        )
        records.append(
            SourceSymbol(
                module,
                node.name,
                node.name,
                "function",
                node.lineno,
                function_args(node),
            )
        )

    if not functions:
        lines.append("| _None declared_ |  |  |  |  |")

    lines += ["", "## Classes", ""]
    if not classes:
        lines.append("_No public classes are declared in this Python source file._")
    for class_node in classes:
        bases = (
            ", ".join(one_line(ast.unparse(base)) for base in class_node.bases)
            or "object"
        )
        class_doc = ast.get_docstring(class_node, clean=True) or ""
        first_doc_line = class_doc.splitlines()[0].strip() if class_doc else ""
        lines += [
            f"### {class_node.name}",
            "",
            f"- Declared at line {class_node.lineno}; bases: {markdown_code(bases)}.",
            f"- Bundled-manual token: {manual_marker(manuals, class_node.name)}.",
        ]
        if first_doc_line:
            lines.append(f"- Source docstring summary: {first_doc_line}")
        records.append(
            SourceSymbol(
                module, class_node.name, class_node.name, "class", class_node.lineno
            )
        )

        methods = direct_functions(class_node.body, include_constructor=True)
        lines += [
            "",
            "| Method | Source signature | Line | Bundled-manual token |",
            "|---|---|---:|---|",
        ]
        for method in methods:
            qualified = f"{class_node.name}.{method.name}"
            lines.append(
                f"| {markdown_code(method.name)} | "
                f"{markdown_code(function_signature(method, qualified))} | {method.lineno} | "
                f"{manual_marker(manuals, method.name)} |"
            )
            if public(method.name):
                records.append(
                    SourceSymbol(
                        module,
                        qualified,
                        method.name,
                        "method",
                        method.lineno,
                        function_args(method),
                    )
                )
        if not methods:
            lines.append("| _None declared_ |  |  |  |")
        lines.append("")

    lines += ["## Callable aliases", ""]
    if aliases:
        lines += [
            "Aliases are assignments visible in the Python source. Whether an aliased native object is callable is not verified statically.",
            "",
            "| Alias | Source target | Line | `__all__` | Bundled-manual token |",
            "|---|---|---:|---|---|",
        ]
        for name, target, line in aliases:
            export_marker = (
                "n/a" if exports is None else ("yes" if name in exports else "no")
            )
            lines.append(
                f"| {markdown_code(name)} | {markdown_code(target)} | {line} | "
                f"{export_marker} | {manual_marker(manuals, name)} |"
            )
            records.append(SourceSymbol(module, name, name, "alias", line))
    else:
        lines.append("_No public aliases were detected._")

    lines += ["", "## Public literal module state", ""]
    if literal_values:
        lines += [
            "These are public literal assignments, not callable APIs.",
            "",
            "| Name | Pinned source value | Line |",
            "|---|---|---:|",
        ]
        for name, value, line in literal_values:
            lines.append(f"| {markdown_code(name)} | {markdown_code(value)} | {line} |")
    else:
        lines.append("_No public literal module state was detected._")

    lines += [
        "",
        "## Limitations",
        "",
        "- Exact-name occurrence in a manual does not prove that the manual is complete or correct for a broker build.",
        "- Source line numbers refer to the file inside the pinned wheel.",
        "- Imported names, especially wildcard imports, are intentionally excluded because their runtime export surface cannot be attributed to this module without importing the package.",
        "",
    ]
    return "\n".join(lines), records


class _FieldVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.fields: list[tuple[str, int]] = []
        self.seen: set[str] = set()

    def _record(self, target: ast.AST) -> None:
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr not in self.seen
        ):
            self.seen.add(target.attr)
            self.fields.append((target.attr, target.lineno))

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record(target)
        self.generic_visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record(node.target)
        if node.value is not None:
            self.generic_visit(node.value)


def class_fields(class_node: ast.ClassDef) -> list[tuple[str, int]]:
    visitor = _FieldVisitor()
    for method in direct_functions(class_node.body, include_constructor=True):
        visitor.visit(method)
    return visitor.fields


def render_xttype(
    source: str, manuals: dict[str, str]
) -> tuple[str, list[SourceSymbol]]:
    tree = ast.parse(source)
    classes = direct_classes(tree)
    records: list[SourceSymbol] = []
    lines = ["# `xtquant.xttype` source-derived type index", ""] + source_header(
        PACKAGE_PATHS["xttype"]
    )
    lines += [
        "Fields below are instance attributes assigned by class methods in the Python wrapper. Native objects returned by the trading client can expose additional types or fields that are documented but absent from this file.",
        "",
    ]

    for class_node in classes:
        records.append(
            SourceSymbol(
                "xttype", class_node.name, class_node.name, "class", class_node.lineno
            )
        )
        bases = (
            ", ".join(one_line(ast.unparse(base)) for base in class_node.bases)
            or "object"
        )
        constructors = [
            node
            for node in direct_functions(class_node.body, include_constructor=True)
            if node.name in {"__new__", "__init__"}
        ]
        fields = class_fields(class_node)
        class_doc = ast.get_docstring(class_node, clean=True) or ""
        first_doc_line = class_doc.splitlines()[0].strip() if class_doc else ""

        lines += [
            f"## {class_node.name}",
            "",
            f"- Declared at line {class_node.lineno}; bases: {markdown_code(bases)}.",
            f"- Bundled-manual token: {manual_marker(manuals, class_node.name)}.",
        ]
        if first_doc_line:
            lines.append(f"- Source docstring summary: {first_doc_line}")
        for constructor in constructors:
            lines.append(
                f"- {constructor.name}: "
                f"{markdown_code(function_signature(constructor, class_node.name + '.' + constructor.name))} "
                f"(line {constructor.lineno})."
            )
        if fields:
            lines.append(
                "- Assigned instance fields: "
                + ", ".join(markdown_code(name) for name, _ in fields)
                + "."
            )
        else:
            lines.append("- Assigned instance fields: none detected.")
        lines.append("")

    lines += [
        "## Limitations",
        "",
        "- This index does not infer runtime types or nullable/value constraints from assignments.",
        "- Several result structures described in `xttrader.md` are supplied by the native client and are not declared in `xttype.py`; the coverage report calls this out.",
        "",
    ]
    return "\n".join(lines), records


def safe_evaluate(node: ast.AST, environment: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return environment[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = safe_evaluate(node.operand, environment)
        return -value if isinstance(node.op, ast.USub) else +value
    if isinstance(node, ast.List):
        return [safe_evaluate(item, environment) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(safe_evaluate(item, environment) for item in node.elts)
    if isinstance(node, ast.Set):
        return {safe_evaluate(item, environment) for item in node.elts}
    if isinstance(node, ast.Dict):
        return {
            safe_evaluate(key, environment): safe_evaluate(value, environment)
            for key, value in zip(node.keys, node.values)
        }
    raise ValueError(f"unsupported constant expression: {type(node).__name__}")


def deterministic_repr(value: Any) -> str:
    if isinstance(value, dict):
        parts = [
            f"{deterministic_repr(key)}: {deterministic_repr(item)}"
            for key, item in value.items()
        ]
        return "{" + ", ".join(parts) + "}"
    if isinstance(value, set):
        return "{" + ", ".join(sorted(deterministic_repr(item) for item in value)) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(deterministic_repr(item) for item in value) + "]"
    if isinstance(value, tuple):
        suffix = "," if len(value) == 1 else ""
        return (
            "(" + ", ".join(deterministic_repr(item) for item in value) + suffix + ")"
        )
    return repr(value)


def render_xtconstant(
    source: str, manuals: dict[str, str]
) -> tuple[str, list[SourceSymbol], dict[str, list[str]]]:
    tree = ast.parse(source)
    environment: dict[str, Any] = {}
    grouped: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    records: list[SourceSymbol] = []
    current_section = "Ungrouped"

    for node in tree.body:
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            heading = " ".join(
                line.strip() for line in node.value.value.splitlines() if line.strip()
            )
            if heading:
                current_section = heading
            continue
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        names = [name for name in assignment_names(node) if public(name)]
        if not names:
            continue
        try:
            value = safe_evaluate(node.value, environment)
            shown_value = deterministic_repr(value)
        except (KeyError, TypeError, ValueError):
            value = None
            shown_value = one_line(ast.unparse(node.value))
        for name in names:
            if value is not None:
                environment[name] = value
            grouped[current_section].append((name, shown_value, node.lineno))
            records.append(
                SourceSymbol("xtconstant", name, name, "constant", node.lineno)
            )

    helper_functions = direct_functions(tree.body)
    lines = [
        "# `xtquant.xtconstant` source-derived constant index",
        "",
    ] + source_header(PACKAGE_PATHS["xtconstant"])
    lines += [
        "Values are evaluated only from literals and references to earlier constants. No package code is executed. Names are grouped by the top-level string section immediately preceding them in the source; inline comments are not reproduced.",
        "",
    ]
    for section, values in grouped.items():
        lines += [
            f"## {section}",
            "",
            "| Name | Pinned value | Line | Bundled-manual token |",
            "|---|---|---:|---|",
        ]
        for name, value, line in values:
            lines.append(
                f"| {markdown_code(name)} | {markdown_code(value)} | {line} | "
                f"{manual_marker(manuals, name)} |"
            )
        lines.append("")

    if helper_functions:
        lines += [
            "## Public helper functions",
            "",
            "| Symbol | Source signature | Line | Bundled-manual token |",
            "|---|---|---:|---|",
        ]
        for node in helper_functions:
            lines.append(
                f"| {markdown_code(node.name)} | {markdown_code(function_signature(node))} | "
                f"{node.lineno} | {manual_marker(manuals, node.name)} |"
            )
            records.append(
                SourceSymbol(
                    "xtconstant",
                    node.name,
                    node.name,
                    "function",
                    node.lineno,
                    function_args(node),
                )
            )
        lines.append("")

    lines += [
        "## Limitations",
        "",
        "- A constant's presence in a bundled manual is only an exact-name token check, not evidence that every broker accepts it.",
        "- Numeric values can be reused across account, order, price, market, and status domains; callers must retain the correct domain.",
        "",
    ]
    return (
        "\n".join(lines),
        records,
        {
            section: [name for name, _, _ in values]
            for section, values in grouped.items()
        },
    )


def strip_receiver(args_text: str | None) -> str | None:
    if args_text is None:
        return None
    try:
        parsed = ast.parse(f"def _f({args_text}):\n    pass\n")
    except SyntaxError:
        return args_text
    args = parsed.body[0].args
    positional = list(args.posonlyargs) + list(args.args)
    if positional and positional[0].arg in {"self", "cls"}:
        if args.posonlyargs:
            args.posonlyargs = args.posonlyargs[1:]
        else:
            args.args = args.args[1:]
    return one_line(ast.unparse(args))


def manual_signature_candidates(text: str) -> dict[str, list[str]]:
    candidates: dict[str, list[str]] = defaultdict(list)
    for raw_line in text.splitlines():
        if raw_line != raw_line.lstrip():
            continue
        line = raw_line.strip().strip("`")
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)", line)
        if not match:
            continue
        name, args = match.groups()
        try:
            parsed = ast.parse(f"def _f({args}):\n    pass\n")
        except SyntaxError:
            continue
        candidates[name].append(one_line(ast.unparse(parsed.body[0].args)))
    return candidates


def symbol_coverage(
    records: list[SourceSymbol], manuals: dict[str, str]
) -> dict[str, tuple[int, int, int, list[SourceSymbol]]]:
    result = {}
    modules = ["xtdata", "xttrader", "xtdatacenter", "xtconstant", "xttype"]
    for module in modules:
        module_records = [record for record in records if record.module == module]
        data_hits = sum(
            exact_token_present(manuals["xtdata.md"], record.leaf)
            for record in module_records
        )
        trader_hits = sum(
            exact_token_present(manuals["xttrader.md"], record.leaf)
            for record in module_records
        )
        missing = [
            record
            for record in module_records
            if not any(
                exact_token_present(text, record.leaf) for text in manuals.values()
            )
        ]
        result[module] = (len(module_records), data_hits, trader_hits, missing)
    return result


def format_symbol_list(records: list[SourceSymbol]) -> list[str]:
    if not records:
        return ["_None._"]
    rendered = [markdown_code(record.qualname) for record in records]
    return [
        ", ".join(rendered[index : index + 8]) for index in range(0, len(rendered), 8)
    ]


def render_coverage(
    records: list[SourceSymbol],
    constant_groups: dict[str, list[str]],
    manuals: dict[str, str],
) -> str:
    coverage = symbol_coverage(records, manuals)
    lines = ["# XTQuant pinned-source API/manual coverage", ""] + source_header(
        "the five pinned Python wrapper modules"
    )
    lines += [
        "## Method",
        "",
        "This report compares statically detected public declarations and aliases with exact identifier-token occurrences in the two Markdown manuals bundled in the same wheel. A token hit is not proof of a complete description or matching behavior; a miss is a useful review signal, not proof that an API is unsupported.",
        "",
        "Coverage includes module functions, public classes and methods, callable aliases, constants, and `xttype` classes. Constructors and instance fields are indexed but excluded from coverage counts. Wildcard imports and runtime/native-only exports are excluded.",
        "",
        "## Summary",
        "",
        "| Source module | Public symbols indexed | Mentioned in `xtdata.md` | Mentioned in `xttrader.md` | Not mentioned in either |",
        "|---|---:|---:|---:|---:|",
    ]
    for module in ["xtdata", "xttrader", "xtdatacenter", "xtconstant", "xttype"]:
        total, data_hits, trader_hits, missing = coverage[module]
        lines.append(
            f"| {markdown_code('xtquant.' + module)} | {total} | {data_hits} | {trader_hits} | {len(missing)} |"
        )

    lines += [
        "",
        "The wheel contains no standalone `xtdatacenter`, `xtconstant`, or `xttype` manual. Their apparent coverage comes from mentions embedded in the data or trading manual.",
        "",
        "## Public callables/types not named in either bundled manual",
        "",
    ]
    for module in ["xtdata", "xttrader", "xtdatacenter", "xttype"]:
        missing = coverage[module][3]
        lines += [f"### `xtquant.{module}`", ""]
        lines.extend(format_symbol_list(missing))
        lines.append("")

    lines += [
        "## Constant coverage by source section",
        "",
        "The complete per-constant token result is in `xtconstant-index.md`; this table keeps the coverage report compact.",
        "",
        "| Source section | Constants | Mentioned in either manual | Not mentioned |",
        "|---|---:|---:|---:|",
    ]
    for section, names in constant_groups.items():
        mentioned = sum(
            any(exact_token_present(text, name) for text in manuals.values())
            for name in names
        )
        lines.append(
            f"| {section.replace('|', '&#124;')} | {len(names)} | {mentioned} | {len(names) - mentioned} |"
        )

    lines += ["", "## Parseable manual signatures versus source", ""]
    source_by_module: dict[str, dict[str, SourceSymbol]] = defaultdict(dict)
    for record in records:
        if (
            record.args is not None
            and record.leaf not in source_by_module[record.module]
        ):
            source_by_module[record.module][record.leaf] = record

    signature_pairs = [
        ("xtdata.md", {"xtdata"}),
        ("xttrader.md", {"xttrader"}),
    ]
    compared = 0
    matched = 0
    mismatches: list[tuple[str, SourceSymbol, str, str]] = []
    for manual_name, modules in signature_pairs:
        candidates = manual_signature_candidates(manuals[manual_name])
        for module in modules:
            for name, record in source_by_module[module].items():
                if name not in candidates:
                    continue
                compared += 1
                source_args = strip_receiver(record.args) or ""
                manual_args = candidates[name][0]
                if source_args == manual_args:
                    matched += 1
                else:
                    mismatches.append((manual_name, record, source_args, manual_args))

    lines += [
        f"Compared {compared} source/manual signatures whose manual declaration was a parseable, unindented Python-style call line. {matched} matched after removing a leading `self`/`cls`; {len(mismatches)} differed.",
        "",
    ]
    if mismatches:
        lines += [
            "| Manual | Symbol | Source parameters | Manual parameters |",
            "|---|---|---|---|",
        ]
        for manual_name, record, source_args, manual_args in mismatches:
            lines.append(
                f"| {markdown_code(manual_name)} | {markdown_code(record.qualname)} | "
                f"{markdown_code(source_args)} | {markdown_code(manual_args)} |"
            )
    else:
        lines.append("_No parseable signature differences were found._")

    lines += ["", "## Manual declarations without a pinned source declaration", ""]
    builtin_names = set(dir(builtins))
    for manual_name, modules in signature_pairs:
        candidates = manual_signature_candidates(manuals[manual_name])
        source_leaves = {
            record.leaf
            for record in records
            if record.module in modules
            and record.kind in {"function", "method", "alias", "class"}
        }
        manual_only = sorted(set(candidates) - source_leaves - builtin_names)
        lines.append(
            f"- {markdown_code(manual_name)}: "
            + (
                ", ".join(markdown_code(name) for name in manual_only)
                if manual_only
                else "none detected"
            )
            + "."
        )

    source_class_names = {
        record.leaf
        for record in records
        if record.kind == "class" and record.module in {"xttrader", "xttype"}
    }
    manual_xt_names = set(
        re.findall(
            r"(?<![A-Za-z0-9_])(Xt[A-Z][A-Za-z0-9_]*)(?![A-Za-z0-9_])",
            manuals["xttrader.md"],
        )
    )
    manual_xt_names.discard("XtQuant")
    manual_only_types = sorted(manual_xt_names - source_class_names)
    lines += [
        "",
        "`Xt...` type-like tokens in `xttrader.md` without a Python class declaration in `xttrader.py` or `xttype.py`: "
        + (
            ", ".join(markdown_code(name) for name in manual_only_types)
            if manual_only_types
            else "none detected"
        )
        + ". These can be native-returned structures rather than stale documentation.",
    ]

    lines += [
        "",
        "## Coverage limitations",
        "",
        "- The comparison is lexical. Common words such as `run` can produce optimistic hits, while translated prose can describe a symbol without naming it.",
        "- Manual signatures are compared only when they appear as an unindented, single-line, parseable Python-style declaration. Parameter annotations and behavioral contracts are not compared.",
        "- Native-returned structures documented in `xttrader.md` but absent from `xttype.py` are outside the pinned Python-source inventory.",
        "- Broker-specific MiniQMT builds can expose a different runtime surface even when the Python wrapper version matches.",
        "",
    ]
    return "\n".join(lines)


def build_outputs(source: dict[str, str], manuals: dict[str, str]) -> dict[str, str]:
    records: list[SourceSymbol] = []
    outputs: dict[str, str] = {}

    for module, filename, title in [
        ("xtdata", "xtdata-api.md", "`xtquant.xtdata` source-derived API inventory"),
        (
            "xttrader",
            "xttrader-api.md",
            "`xtquant.xttrader` source-derived API inventory",
        ),
        (
            "xtdatacenter",
            "xtdatacenter-api.md",
            "`xtquant.xtdatacenter` source-derived API inventory",
        ),
    ]:
        rendered, module_records = render_callable_module(
            module, title, source[module], manuals
        )
        outputs[filename] = rendered
        records.extend(module_records)

    type_output, type_records = render_xttype(source["xttype"], manuals)
    outputs["xttype-index.md"] = type_output
    records.extend(type_records)

    constant_output, constant_records, constant_groups = render_xtconstant(
        source["xtconstant"], manuals
    )
    outputs["xtconstant-index.md"] = constant_output
    records.extend(constant_records)

    outputs["api-doc-coverage.md"] = render_coverage(records, constant_groups, manuals)
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help=f"path to xtquant-{VERSION} wheel")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify checked-in Markdown matches regenerated output without writing",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source, manuals = read_wheel(args.wheel)
    outputs = build_outputs(source, manuals)
    destination = Path(__file__).resolve().parent

    if args.check:
        mismatches = []
        for name, expected in outputs.items():
            path = destination / name
            actual = path.read_text(encoding="utf-8") if path.exists() else None
            if actual != expected:
                mismatches.append(name)
        if mismatches:
            print(
                "generated Markdown differs: " + ", ".join(mismatches), file=sys.stderr
            )
            return 1
        print(f"verified {len(outputs)} generated Markdown files")
        return 0

    for name, content in outputs.items():
        with (destination / name).open("w", encoding="utf-8", newline="\n") as target:
            target.write(content)
    print(f"wrote {len(outputs)} generated Markdown files to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
