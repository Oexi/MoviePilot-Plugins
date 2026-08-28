import copy
import importlib.util
import unittest
import xml.dom.minidom
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "plugins.v3" / "prowlarrextend"
FIXTURES = ROOT / "tests" / "fixtures"


def load_helper(filename, name):
    spec = importlib.util.spec_from_file_location(name, PACKAGE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INDEXERS = load_helper("_indexers.py", "prowlarrextend_indexers_isolated")
TORZNAB = load_helper("_torznab.py", "prowlarrextend_torznab_isolated")


class ProwlarrIndexerHelperTest(unittest.TestCase):
    def test_profiles_filter_api_rows_and_flatten_nested_categories(self):
        raw = [
            {
                "id": 42,
                "name": "Public Torrent",
                "enable": True,
                "protocol": "torrent",
                "privacy": "public",
                "supportsSearch": True,
                "capabilities": {
                    "categories": [
                        {
                            "id": 2000,
                            "name": "Movies",
                            "subCategories": [{"id": 2010, "name": "Foreign"}],
                        },
                        {"id": 5000, "name": "TV"},
                        {"id": 3000, "name": "Audio"},
                    ]
                },
            },
            {"id": 43, "name": "Disabled", "enable": False, "protocol": "torrent", "supportsSearch": True},
            {"id": 44, "name": "Usenet", "enable": True, "protocol": "usenet", "supportsSearch": True},
            {"id": 45, "name": "No search", "enable": True, "protocol": "torrent", "supportsSearch": False},
        ]
        profiles = INDEXERS.build_indexer_profiles(raw, "https://prowlarr.invalid/", True)
        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual(profile["indexer_id"], "42")
        self.assertEqual(profile["url"], "https://prowlarr.invalid/api/v1/indexer/42/newznab")
        self.assertEqual(profile["domain"], "prowlarr_extend.42")
        self.assertEqual(profile["plugin"], "ProwlarrExtend")
        self.assertEqual(profile["parser"], "ProwlarrExtend")
        self.assertTrue(profile["public"])
        self.assertEqual(profile["privacy"], "public")
        self.assertEqual([x["id"] for x in profile["category"]["movie"]], ["2000", "2010"])

    def test_semi_private_and_id_validation(self):
        self.assertEqual(INDEXERS.normalize_privacy("semiPrivate"), "semi-private")
        self.assertEqual(INDEXERS.normalize_indexer_id("00042"), "42")
        for value in (0, -1, True, 1.0, "42/path", str(2_147_483_648)):
            self.assertFalse(INDEXERS.is_valid_indexer_id(value), value)
        self.assertEqual(INDEXERS.parse_indexer_sites("['42', ' 42 ', '99', 'not-id']"), ["42", "99"])
        self.assertEqual(INDEXERS.indexer_id_from_domain("PROWLARR_EXTEND.42"), "42")
        self.assertEqual(INDEXERS.indexer_id_from_domain("prowlarr_extend.not-id"), "")
        self.assertTrue(INDEXERS.is_virtual_site({"domain": "prowlarr_extend.42"}))
        self.assertFalse(INDEXERS.is_virtual_site({"domain": "jackett_extend.42"}))
        self.assertFalse(INDEXERS.is_virtual_site({
            "domain": "prowlarr_extend.42",
            "plugin": "JackettExtend",
        }))
        self.assertTrue(INDEXERS.is_virtual_site({
            "domain": "jackett_extend.42",
            "parser": "ProwlarrExtend",
        }))

    def test_selection_does_not_mutate_profiles(self):
        profiles = [{"indexer_id": "42", "nested": {"value": 1}}, {"indexer_id": "99"}]
        original = copy.deepcopy(profiles)
        selected = INDEXERS.apply_indexer_selection(profiles, [42], explicit=True)
        self.assertEqual(selected, [profiles[0]])
        self.assertEqual(profiles, original)
        self.assertEqual(INDEXERS.apply_indexer_selection(profiles, "missing", explicit=True), [])


class ProwlarrTorznabHelperTest(unittest.TestCase):
    def test_url_builder_encodes_query_and_rejects_bad_ids(self):
        url = TORZNAB.build_torznab_url(
            "https://prowlarr.invalid/",
            42,
            "not-a-real-key",
            "Title / Part & Two",
            cat="2000,2010",
        )
        self.assertEqual(urlsplit(url).path, "/api/v1/indexer/42/newznab")
        self.assertEqual(parse_qs(urlsplit(url).query), {
            "t": ["search"],
            "q": ["Title / Part & Two"],
            "cat": ["2000,2010"],
        })
        self.assertNotIn("not-a-real-key", url)
        self.assertEqual(TORZNAB.build_torznab_url("https://prowlarr.invalid", "42/path", "k", "q"), "")

    def test_fixture_extraction_and_http_preference(self):
        document = xml.dom.minidom.parse(str(FIXTURES / "prowlarr_extend_private_http.xml"))
        fields = TORZNAB.extract_torznab_item(document.getElementsByTagName("item")[0])
        self.assertEqual(fields["title"], "Prowlarr private fixture")
        self.assertEqual(fields["enclosure"], "https://prowlarr.invalid/download/private.torrent")
        self.assertEqual(fields["seeders"], "12")
        self.assertEqual(
            TORZNAB.select_torznab_enclosure(
                enclosure="magnet:?xt=urn:btih:x",
                link="https://prowlarr.invalid/download/x.torrent",
            ),
            "https://prowlarr.invalid/download/x.torrent",
        )
        self.assertEqual(
            TORZNAB.select_torznab_enclosure(
                enclosure="magnet:?xt=urn:btih:x",
                link="https://prowlarr.invalid/details/x",
            ),
            "magnet:?xt=urn:btih:x",
        )

    def test_current_prowlarr_yts_imdb_attribute_is_canonicalized(self):
        # Prowlarr's current Newznab response uses ``imdb`` with a numeric,
        # zero-padded ID; the fixture intentionally contains no live title,
        # URL, API key, or tracker identity.
        document = xml.dom.minidom.parse(str(FIXTURES / "prowlarr_extend_yts.xml"))
        fields = TORZNAB.extract_torznab_item(document.getElementsByTagName("item")[0])

        self.assertEqual(fields["imdbid"], "tt0123456")
        self.assertEqual(fields["infohash"], "0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(fields["seeders"], "123")
        self.assertEqual(fields["peers"], "4")

    def test_dedupe_numeric_safety_response_classification_and_redaction(self):
        rows = [
            {"infohash": "ABC", "enclosure": "magnet:?xt=urn:btih:abc", "title": "magnet"},
            {"infohash": "abc", "enclosure": "https://prowlarr.invalid/x.torrent", "title": "http"},
        ]
        deduped = TORZNAB.dedupe_torznab_items(rows)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["title"], "http")
        self.assertEqual(TORZNAB.safe_float("nan"), 0.0)
        self.assertEqual(TORZNAB.safe_count("-1"), 0)
        self.assertEqual(TORZNAB.classify_torznab_response(200, "application/json", "{}"), "json")
        self.assertEqual(TORZNAB.classify_torznab_response(200, "application/xml", "<rss/>"), "ok")
        redacted = TORZNAB.redact_url(
            "https://prowlarr.invalid/newznab?apikey=redact-me&api_key=also-redact&token=opaque&q=private+title&cat=2000"
        )
        for secret in ("redact-me", "also-redact", "opaque", "private", "title"):
            self.assertNotIn(secret, redacted)
        self.assertIn("apikey=%2A%2A%2A", redacted)
        self.assertIn("cat=2000", redacted)


class ProwlarrUiHelperTest(unittest.TestCase):
    def test_form_has_current_configuration_only_and_prewires_port(self):
        # Relative import requires a tiny package shim, while still avoiding
        # any MoviePilot module installation.
        import sys
        import types
        package = types.ModuleType("prowlarrextend_ui_pkg")
        package.__path__ = [str(PACKAGE)]
        sys.modules[package.__name__] = package
        indexer_spec = importlib.util.spec_from_file_location(
            "prowlarrextend_ui_pkg._indexers", PACKAGE / "_indexers.py"
        )
        indexer_module = importlib.util.module_from_spec(indexer_spec)
        sys.modules[indexer_spec.name] = indexer_module
        indexer_spec.loader.exec_module(indexer_module)
        spec = importlib.util.spec_from_file_location(
            "prowlarrextend_ui_pkg._ui", PACKAGE / "_ui.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        form, defaults = module.build_form([{"title": "Public (42)", "value": "42"}])
        models = []
        texts = []

        def walk(value):
            if isinstance(value, dict):
                props = value.get("props", {})
                if "model" in props:
                    models.append(props["model"])
                texts.extend(str(v) for v in props.values() if isinstance(v, str))
                for child in value.get("content", []):
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(form)
        self.assertEqual(set(models), {"enabled", "proxy", "host", "api_key", "cron", "timeout", "indexer_sites"})
        self.assertEqual(defaults["host"], "")
        self.assertNotIn("password", models)
        self.assertTrue(any("9696" in text for text in texts))


if __name__ == "__main__":
    unittest.main()
