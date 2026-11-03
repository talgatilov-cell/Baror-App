"""Static guards for the layering rules in CLAUDE.md."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def imports_of(path: Path) -> set:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


class Layering(unittest.TestCase):
    def test_pure_layers_never_import_streamlit_or_third_party_packages(self):
        allowed_third_party = set()                       # these layers are standard library + baror only
        stdlib = set(__import__("sys").stdlib_module_names) | {"baror", "__future__"}
        for layer in ("domain", "storage", "auth"):
            for f in (ROOT / "baror" / layer).glob("*.py"):
                bad = imports_of(f) - stdlib - allowed_third_party
                self.assertEqual(bad, set(), f"{f.relative_to(ROOT)} imports {bad}")
        for f in (ROOT / "baror").glob("*.py"):
            if f.name in ("errors.py",):
                self.assertEqual(imports_of(f) - stdlib, set(), f.name)

    def test_scripts_use_the_standard_library_only(self):
        stdlib = set(__import__("sys").stdlib_module_names) | {"baror", "__future__"}
        siblings = {f.stem for f in (ROOT / "scripts").glob("*.py")}     # scripts may import each other (retention -> backup)
        for f in (ROOT / "scripts").glob("*.py"):
            self.assertEqual(imports_of(f) - stdlib - siblings, set(), f.name)


if __name__ == "__main__":
    unittest.main()
