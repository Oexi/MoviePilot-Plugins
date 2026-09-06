import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from importlib import import_module


class StringUtilsStub:
    clear_calls = []

    @staticmethod
    def clear(text, replace_word="", allow_space=False):
        StringUtilsStub.clear_calls.append((text, replace_word, allow_space))
        if text == "My Soul,Your Beats!/Brave Song":
            return "My Soul Your Beats Brave Song"
        if text == "Test123, S!":
            return "Test123 S"
        if text == "落第賢者の学院無双 ~二度目の転生、Sランクチート魔術師冒険録~":
            return "落第賢者の学院無双 二度目の転生 Sランクチート魔術師冒険録"
        return text

    @staticmethod
    def get_url_domain(value):
        return value


class LoggerStub:
    messages = []

    def __getattr__(self, _name):
        return lambda message, *args, **kwargs: self.messages.append(str(message))


@contextmanager
def loaded_plugin_module():
    module = import_module("app.plugins.jackettextend")
    patched = {
        "StringUtils": StringUtilsStub,
        "logger": LoggerStub(),
        "settings": SimpleNamespace(PROXY=None),
    }
    previous = {name: getattr(module, name) for name in patched}
    try:
        for name, value in patched.items():
            setattr(module, name, value)
        yield module
    finally:
        for name, value in previous.items():
            setattr(module, name, value)


class JackettSearchKeywordTest(unittest.TestCase):
    def setUp(self):
        StringUtilsStub.clear_calls.clear()
        LoggerStub.messages.clear()

    @staticmethod
    def run_search(module, keyword):
        plugin = object.__new__(module.JackettExtend)
        plugin._api_key = "test-key"
        plugin._host = "http://jackett.invalid"
        captured = {}

        def parse_torznab(url, **kwargs):
            captured["url"] = url
            captured["keyword"] = kwargs["keyword"]
            return []

        setattr(plugin, "_JackettExtend__parse_torznab_xml", parse_torznab)
        plugin.search_torrents(
            site={"name": "Nyaa", "domain": "jackett_extend.nyaa"},
            keyword=keyword,
        )
        return captured

    def test_punctuation_is_normalized_before_torznab_query(self):
        with loaded_plugin_module() as module:
            captured = self.run_search(module, "My Soul,Your Beats!/Brave Song")

        query = parse_qs(urlparse(captured["url"]).query)
        self.assertEqual(query["q"], ["My Soul Your Beats Brave Song"])
        self.assertEqual(captured["keyword"], "My Soul Your Beats Brave Song")
        self.assertEqual(
            StringUtilsStub.clear_calls,
            [("My Soul,Your Beats!/Brave Song", " ", True)],
        )

    def test_empty_keyword_remains_a_valid_empty_query(self):
        for keyword in (None, ""):
            with self.subTest(keyword=keyword), loaded_plugin_module() as module:
                captured = self.run_search(module, keyword)

            query = parse_qs(urlparse(captured["url"]).query, keep_blank_values=True)
            self.assertEqual(query["q"], [""])
            self.assertEqual(captured["keyword"], "")

    def test_full_width_keyword_is_nfkc_normalized_before_cleaning_and_query(self):
        with loaded_plugin_module() as module:
            captured = self.run_search(module, "Ｔｅｓｔ１２３， Ｓ！")

        query = parse_qs(urlparse(captured["url"]).query)
        self.assertEqual(query["q"], ["Test123 S"])
        self.assertEqual(captured["keyword"], "Test123 S")
        self.assertEqual(
            StringUtilsStub.clear_calls,
            [("Test123, S!", " ", True)],
        )

    def test_real_world_full_width_title_is_cleaned_for_torznab_query(self):
        title = "落第賢者の学院無双 ～二度目の転生、Ｓランクチート魔術師冒険録～"
        expected = "落第賢者の学院無双 二度目の転生 Sランクチート魔術師冒険録"
        with loaded_plugin_module() as module:
            captured = self.run_search(module, title)

        query = parse_qs(urlparse(captured["url"]).query)
        self.assertEqual(query["q"], [expected])
        self.assertEqual(captured["keyword"], expected)

    def test_already_normalized_keyword_is_unchanged(self):
        for keyword in ("My Soul Your Beats Brave Song", "落第賢者の学院無双 Sランク"):
            with self.subTest(keyword=keyword), loaded_plugin_module() as module:
                captured = self.run_search(module, keyword)

            query = parse_qs(urlparse(captured["url"]).query)
            self.assertEqual(query["q"], [keyword])
            self.assertEqual(captured["keyword"], keyword)

    def test_exact_profile_indexer_id_wins_over_decoded_domain(self):
        with loaded_plugin_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._api_key = "test-key"
            plugin._host = "http://jackett.invalid"
            captured = {}

            def parse_torznab(url, **kwargs):
                captured["url"] = url
                return []

            setattr(plugin, "_JackettExtend__parse_torznab_xml", parse_torznab)
            plugin.search_torrents(
                site={
                    "name": "Special",
                    "domain": "jackett_extend.lowered%2Fdomain",
                    "indexer_id": "Exact.ID/Path",
                },
                keyword="x",
            )
            self.assertIn("/indexers/Exact.ID%2FPath/results/", captured["url"])

    def test_search_exception_log_never_contains_keyword_text(self):
        with loaded_plugin_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._api_key = "test-key"
            plugin._host = "http://jackett.invalid"

            def parse_torznab(*_args, **_kwargs):
                raise RuntimeError("request failed")

            setattr(plugin, "_JackettExtend__parse_torznab_xml", parse_torznab)
            plugin.search_torrents(
                site={"name": "Nyaa", "domain": "jackett_extend.nyaa"},
                keyword="PrivateSearchPhrase",
            )

            rendered = "\n".join(LoggerStub.messages)
            self.assertNotIn("PrivateSearchPhrase", rendered)
            self.assertNotIn("Private", rendered)
            self.assertNotIn("SearchPhrase", rendered)


if __name__ == "__main__":
    unittest.main()
