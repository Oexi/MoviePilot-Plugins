import importlib.util
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PATH = ROOT / "plugins.v3" / "jackettextend"


class StringUtilsStub:
    clear_calls = []

    @staticmethod
    def clear(text, replace_word="", allow_space=False):
        StringUtilsStub.clear_calls.append((text, replace_word, allow_space))
        if text == "My Soul,Your Beats!/Brave Song":
            return "My Soul Your Beats Brave Song"
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
    stubs = {
        "apscheduler": types.ModuleType("apscheduler"),
        "apscheduler.schedulers": types.ModuleType("apscheduler.schedulers"),
        "apscheduler.schedulers.background": types.ModuleType("apscheduler.schedulers.background"),
        "apscheduler.triggers": types.ModuleType("apscheduler.triggers"),
        "apscheduler.triggers.cron": types.ModuleType("apscheduler.triggers.cron"),
        "app": types.ModuleType("app"),
        "app.plugins": types.ModuleType("app.plugins"),
        "app.schemas": types.ModuleType("app.schemas"),
        "app.schemas.types": types.ModuleType("app.schemas.types"),
        "app.sdk": types.ModuleType("app.sdk"),
        "app.sdk.config": types.ModuleType("app.sdk.config"),
        "app.sdk.logging": types.ModuleType("app.sdk.logging"),
        "app.sdk.media": types.ModuleType("app.sdk.media"),
        "app.sdk.network": types.ModuleType("app.sdk.network"),
        "app.sdk.utilities": types.ModuleType("app.sdk.utilities"),
    }
    stubs["apscheduler.schedulers.background"].BackgroundScheduler = object
    stubs["apscheduler.triggers.cron"].CronTrigger = object
    stubs["app.plugins"]._PluginBase = object
    stubs["app.schemas"].MediaType = object
    stubs["app.schemas"].__path__ = []
    stubs["app.schemas.types"].MediaSource = object
    stubs["app.sdk"].__path__ = []
    stubs["app.sdk.config"].settings = types.SimpleNamespace(PROXY=None)
    stubs["app.sdk.logging"].logger = LoggerStub()
    stubs["app.sdk.media"].TorrentInfo = object
    stubs["app.sdk.network"].RequestUtils = object
    stubs["app.sdk.network"].SitesHelper = object
    stubs["app.sdk.utilities"].DomUtils = object
    stubs["app.sdk.utilities"].StringUtils = StringUtilsStub

    previous = {name: sys.modules.get(name) for name in stubs}
    package_name = "jackettextend_search_test"
    previous_package = sys.modules.get(package_name)
    try:
        sys.modules.update(stubs)
        spec = importlib.util.spec_from_file_location(
            package_name,
            PACKAGE_PATH / "__init__.py",
            submodule_search_locations=[str(PACKAGE_PATH)],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        for name, previous_module in previous.items():
            if previous_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous_module
        if previous_package is None:
            sys.modules.pop(package_name, None)
        else:
            sys.modules[package_name] = previous_package
        for name in list(sys.modules):
            if name.startswith(f"{package_name}."):
                sys.modules.pop(name, None)


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

    def test_already_normalized_keyword_is_unchanged(self):
        with loaded_plugin_module() as module:
            captured = self.run_search(module, "My Soul Your Beats Brave Song")

        query = parse_qs(urlparse(captured["url"]).query)
        self.assertEqual(query["q"], ["My Soul Your Beats Brave Song"])
        self.assertEqual(captured["keyword"], "My Soul Your Beats Brave Song")

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
