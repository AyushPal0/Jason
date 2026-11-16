"""Extract Python structure using syntax trees, without executing source."""

import ast


class StructureVisitor(ast.NodeVisitor):
    """Collect definitions and imports in source traversal order."""

    def __init__(self) -> None:
        self.scope: list[str] = []
        self.symbols: list[dict] = []
        self.imports: list[dict] = []

    def visit_definition(self, node, kind: str) -> None:
        qualified_name = ".".join([*self.scope, node.name])
        self.symbols.append({
            "name": node.name,
            "qualified_name": qualified_name,
            "kind": kind,
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "start_line": min([node.lineno, *(d.lineno for d in node.decorator_list)]),
            "definition_line": node.lineno,
            "end_line": node.end_lineno,
        })
        self.scope.append(node.name)
        # Only the body belongs to the new scope; decorators/defaults/bases
        # are evaluated outside it and contain no statement definitions.
        for statement in node.body:
            self.visit(statement)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.visit_definition(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.visit_definition(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_definition(node, "function")

    def visit_Import(self, node: ast.Import) -> None:
        self.add_import(node, module=None, level=0)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.add_import(node, module=node.module, level=node.level)

    def add_import(self, node, *, module: str | None, level: int) -> None:
        self.imports.append({
            "kind": "from" if isinstance(node, ast.ImportFrom) else "import",
            "module": module,
            "level": level,
            "names": [{"name": alias.name, "alias": alias.asname} for alias in node.names],
            "scope": ".".join(self.scope) or None,
            "start_line": node.lineno,
            "end_line": node.end_lineno,
        })


def parse_python(source: str, *, filename: str = "<source>") -> dict:
    """Return definitions, imports, and a structured error for invalid syntax.

    Locations use one-based, inclusive source lines. Parsing follows the
    running interpreter's grammar. Imports are syntax evidence, not resolved
    dependencies or proof that an import executes at runtime.
    """
    try:
        tree = ast.parse(source, filename=filename)
        visitor = StructureVisitor()
        visitor.visit(tree)
    except SyntaxError as error:
        return {
            "status": "error", "symbols": [], "imports": [],
            "error": {"kind": "syntax_error", "message": error.msg, "line": error.lineno},
        }
    except (RecursionError, ValueError) as error:
        return {
            "status": "error", "symbols": [], "imports": [],
            "error": {"kind": "parse_error", "message": str(error), "line": None},
        }
    return {"status": "ok", "symbols": visitor.symbols, "imports": visitor.imports, "error": None}
