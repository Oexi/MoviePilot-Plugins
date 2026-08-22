import importlib.util
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "plugins.v3" / "jackettextend" / "_torznab.py"
FIXTURES = ROOT / "tests" / "fixtures"
TORZNAB_NS = "{http://torznab.com/schemas/2015/feed}"

SPEC = importlib.util.spec_from_file_location("jackettextend_torznab", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture_fields(name):
    item = ElementTree.parse(FIXTURES / name).find(".//item")
    attrs = {
        node.get("name"): node.get("value")
        for node in item.findall(f"{TORZNAB_NS}attr")
    }
    return {
        "enclosure": item.find("enclosure").get("url"),
        "link": item.findtext("link"),
        "guid": item.findtext("guid"),
        "comments": item.findtext("comments"),
        "magnet_url": attrs.get("magneturl"),
        "files": attrs.get("files"),
    }


class TorznabEnclosureTest(unittest.TestCase):
    def test_private_indexer_direct_download_is_preserved(self):
        fields = fixture_fields("jackettextend_private_http.xml")

        self.assertEqual(
            MODULE.select_torznab_enclosure(
                enclosure=fields["enclosure"],
                link=fields["link"],
                guid=fields["guid"],
            ),
            fields["enclosure"],
        )

    def test_public_indexer_magnet_only_result_stays_magnet(self):
        fields = fixture_fields("jackettextend_public_magnet.xml")

        selected = MODULE.select_torznab_enclosure(
            enclosure=fields["enclosure"],
            link=fields["link"],
            magnet_url=fields["magnet_url"],
            guid=fields["guid"],
        )

        self.assertEqual(selected, fields["enclosure"])
        self.assertTrue(selected.startswith("magnet:"))
        self.assertEqual(fields["files"], "23")
        self.assertNotIn("files=", selected)
        self.assertNotIn(".torrent", selected)

    def test_http_link_wins_when_enclosure_is_magnet(self):
        direct = "https://jackett.invalid/dl/indexer/?path=protected"
        self.assertEqual(
            MODULE.select_torznab_enclosure(
                enclosure="magnet:?xt=urn:btih:fixture",
                link=direct,
                magnet_url="magnet:?xt=urn:btih:fixture",
            ),
            direct,
        )

    def test_direct_torrent_url_is_preserved_exactly(self):
        direct = "https://jackett.invalid/download/release.torrent?ticket=opaque"
        self.assertEqual(
            MODULE.select_torznab_enclosure(enclosure=direct),
            direct,
        )

    def test_detail_and_infohash_cannot_be_promoted_to_download_url(self):
        fields = fixture_fields("jackettextend_public_magnet.xml")

        selected = MODULE.select_torznab_enclosure(
            enclosure="",
            link="",
            magnet_url="",
            guid=fields["comments"],
        )

        self.assertEqual(selected, "")

    def test_unauthorized_json_and_empty_responses_are_rejected(self):
        cases = (
            (401, "application/json", '{"error":"unauthorized"}'),
            (403, "text/html", "<html>forbidden</html>"),
            (200, "application/json", '{"error":"bad request"}'),
            (200, "application/xml", ""),
        )
        for status, content_type, body in cases:
            with self.subTest(status=status, content_type=content_type):
                self.assertFalse(
                    MODULE.is_usable_torznab_response(status, content_type, body)
                )

    def test_log_url_redacts_credentials(self):
        redacted = MODULE.redact_url(
            "http://jackett.invalid/results?apikey=secret&cat=3000&token=hidden"
        )
        self.assertNotIn("secret", redacted)
        self.assertNotIn("hidden", redacted)
        self.assertIn("cat=3000", redacted)


if __name__ == "__main__":
    unittest.main()
