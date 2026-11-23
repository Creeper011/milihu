import ast
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "milihu"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_layer_constants_are_defined_in_types(self) -> None:
        for folder in ("services", "usecases", "discord"):
            for path in sorted((SRC_ROOT / folder).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in tree.body:
                    if isinstance(node, ast.Assign):
                        targets = node.targets
                    elif isinstance(node, ast.AnnAssign):
                        targets = [node.target]
                    else:
                        continue
                    for target in targets:
                        if isinstance(target, ast.Name):
                            self.assertFalse(
                                target.id.isupper() and target.id != "_LOGGER",
                                f"{path.relative_to(SRC_ROOT)}:{node.lineno}: "
                                f"move {target.id} to types/",
                            )

    def _assert_no_imports(self, folder: str, *forbidden: str) -> None:
        paths = sorted((SRC_ROOT / folder).rglob("*.py"))
        self.assertTrue(paths, f"No modules found in {folder}/")
        for path in paths:
            for module in _imported_modules(path):
                for prefix in forbidden:
                    self.assertFalse(
                        module == prefix or module.startswith(f"{prefix}."),
                        f"{path.relative_to(SRC_ROOT)} must not import {module}",
                    )

    def test_usecases_do_not_import_services_or_aiohttp(self) -> None:
        self._assert_no_imports("usecases", "milihu.services", "aiohttp")

    def test_cogs_do_not_import_usecases_services_or_aiohttp(self) -> None:
        self._assert_no_imports(
            "discord/cogs", "milihu.usecases", "milihu.services", "aiohttp"
        )

    def test_types_and_faces_do_not_import_usecases_services_or_aiohttp(self) -> None:
        for folder in ("types", "faces"):
            self._assert_no_imports(
                folder, "milihu.usecases", "milihu.services", "aiohttp"
            )

    def test_domain_layers_do_not_import_discord(self) -> None:
        for folder in ("types", "faces", "usecases"):
            self._assert_no_imports(folder, "discord")
