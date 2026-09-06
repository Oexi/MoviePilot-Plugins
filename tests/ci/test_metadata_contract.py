import ast
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_METADATA = {
    "JackettExtend": (ROOT / "plugins.v3" / "jackettextend" / "__init__.py", "JackettExtend"),
    "ProwlarrExtend": (ROOT / "plugins.v3" / "prowlarrextend" / "__init__.py", "ProwlarrExtend"),
}
CLASS_FIELDS = ("plugin_name", "plugin_version", "plugin_author")


def _class_metadata(source_path, class_name):
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    class_nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    ]
    if len(class_nodes) != 1:
        raise AssertionError(f"expected one {class_name} class in {source_path}")

    metadata = {}
    for statement in class_nodes[0].body:
        if not isinstance(statement, ast.Assign):
            continue
        for target in statement.targets:
            if isinstance(target, ast.Name) and target.id in CLASS_FIELDS:
                metadata[target.id] = ast.literal_eval(statement.value)
    return metadata


class MetadataContractTest(unittest.TestCase):
    def test_manifest_metadata_matches_plugin_class_metadata(self):
        manifest = json.loads((ROOT / "package.v3.json").read_text(encoding="utf-8"))

        for plugin_name, (source_path, class_name) in PLUGIN_METADATA.items():
            with self.subTest(plugin=plugin_name):
                manifest_metadata = manifest[plugin_name]
                class_metadata = _class_metadata(source_path, class_name)

                self.assertEqual(manifest_metadata["name"], class_metadata["plugin_name"])
                self.assertEqual(manifest_metadata["version"], class_metadata["plugin_version"])
                self.assertEqual(manifest_metadata["author"], class_metadata["plugin_author"])
                self.assertEqual(manifest_metadata["author"], "oexi")
                self.assertEqual(manifest_metadata["system_version"], ">=3.0.0")
