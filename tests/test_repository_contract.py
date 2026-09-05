import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIRS = (
    ROOT / "plugins.v3" / "jackettextend",
    ROOT / "plugins.v3" / "prowlarrextend",
)
SHARED_MODULES = ("_host_compat.py", "_site_registry.py", "_torznab_core.py", "_response.py")


class RepositoryContractTest(unittest.TestCase):
    def test_shared_modules_match_repository_canonical_sources(self):
        for module_name in SHARED_MODULES:
            canonical = (ROOT / "shared" / module_name).read_bytes()
            for plugin_dir in PLUGIN_DIRS:
                with self.subTest(module=module_name, plugin=plugin_dir.name):
                    self.assertEqual((plugin_dir / module_name).read_bytes(), canonical)

    def test_plugin_entrypoints_keep_site_persistence_behind_adapter(self):
        forbidden_modules = {"app.db.oper.site", "app.sdk.events"}
        for plugin_dir in PLUGIN_DIRS:
            source_path = plugin_dir / "__init__.py"
            source = source_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(source_path))
            imported_modules = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
            with self.subTest(plugin=plugin_dir.name):
                self.assertTrue(forbidden_modules.isdisjoint(imported_modules))
                self.assertIn("open_site_registry", source)

        jackett_source = (PLUGIN_DIRS[0] / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("jackett_domain", jackett_source)


if __name__ == "__main__":
    unittest.main()
