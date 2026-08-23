import asyncio
import importlib.util
import inspect
import sys
import types
import unittest
import xml.dom.minidom
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PATH = ROOT / "plugins.v3" / "jackettextend"


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class _MediaType:
    MUSIC = types.SimpleNamespace(value="音乐", name="MUSIC")


class _MediaSource:
    IMDb = "imdb"


class _TorrentInfo:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _StringUtils:
    @staticmethod
    def clear(text, replace_word="", allow_space=False):
        return text

    @staticmethod
    def unify_datetime_str(value):
        return value

    @staticmethod
    def get_url_domain(value):
        return value


class _DomUtils:
    @staticmethod
    def tag_value(node, tag, attr=None, default=None):
        elements = node.getElementsByTagName(tag)
        if not elements:
            return default
        element = elements[0]
        if attr:
            return element.getAttribute(attr) or default
        return "".join(child.data for child in element.childNodes if child.nodeType == child.TEXT_NODE)


class _Response:
    status_code = 200
    headers = {"Content-Type": "application/xml"}
    text = ""


class _RequestUtils:
    response = _Response()
    timeouts = []

    def __init__(self, *args, **kwargs):
        self.timeouts.append(kwargs.get("timeout"))

    def get_res(self, *args, **kwargs):
        return self.response


@contextmanager
def loaded_module():
    names = {
        "apscheduler": types.ModuleType("apscheduler"),
        "apscheduler.schedulers": types.ModuleType("apscheduler.schedulers"),
        "apscheduler.schedulers.background": types.ModuleType("apscheduler.schedulers.background"),
        "apscheduler.triggers": types.ModuleType("apscheduler.triggers"),
        "apscheduler.triggers.cron": types.ModuleType("apscheduler.triggers.cron"),
        "app": types.ModuleType("app"),
        "app.helper": types.ModuleType("app.helper"),
        "app.helper.sites": types.ModuleType("app.helper.sites"),
        "app.plugins": types.ModuleType("app.plugins"),
        "app.schemas": types.ModuleType("app.schemas"),
        "app.sdk": types.ModuleType("app.sdk"),
        "app.sdk.config": types.ModuleType("app.sdk.config"),
        "app.sdk.logging": types.ModuleType("app.sdk.logging"),
        "app.sdk.media": types.ModuleType("app.sdk.media"),
        "app.sdk.network": types.ModuleType("app.sdk.network"),
        "app.sdk.utilities": types.ModuleType("app.sdk.utilities"),
    }
    names["apscheduler.schedulers.background"].BackgroundScheduler = object
    names["apscheduler.triggers.cron"].CronTrigger = object
    names["app.helper.sites"].SitesHelper = object
    names["app.plugins"]._PluginBase = object
    names["app.schemas"].MediaType = _MediaType
    names["app.schemas"].MediaSource = _MediaSource
    names["app.sdk.config"].settings = types.SimpleNamespace(PROXY=None, TZ="UTC", USER_AGENT="test")
    names["app.sdk.logging"].logger = _Logger()
    names["app.sdk.media"].TorrentInfo = _TorrentInfo
    names["app.sdk.network"].RequestUtils = _RequestUtils
    names["app.sdk.utilities"].DomUtils = _DomUtils
    names["app.sdk.utilities"].StringUtils = _StringUtils
    previous = {name: sys.modules.get(name) for name in names}
    package_name = "jackettextend_v3_test"
    previous_package = sys.modules.get(package_name)
    _RequestUtils.timeouts.clear()
    try:
        sys.modules.update(names)
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
        sys.modules.pop(f"{package_name}._torznab", None)


class JackettV3ContractTest(unittest.TestCase):
    def test_async_module_is_a_real_threadpool_provider(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            calls = []
            threadpool_calls = []

            def search(**kwargs):
                calls.append(kwargs)
                return ["ok"]

            async def fake_threadpool(func, *args, **kwargs):
                threadpool_calls.append(func)
                return func(*args, **kwargs)

            module.run_in_threadpool = fake_threadpool
            plugin.search_torrents = search
            result = asyncio.run(plugin.async_search_torrents(site={"domain": "jackett_extend.nyaa"}, keyword="x"))

            self.assertEqual(result, ["ok"])
            self.assertEqual(threadpool_calls, [search])
            self.assertEqual(calls[0]["site"]["domain"], "jackett_extend.nyaa")
            self.assertTrue(inspect.iscoroutinefunction(module.JackettExtend.async_search_torrents))
            self.assertTrue(inspect.iscoroutinefunction(module.JackettExtend.get_module(plugin)["async_search_torrents"]))

    def test_timeout_is_bounded_and_written_to_form_defaults(self):
        with loaded_module() as module:
            self.assertEqual(module.JackettExtend._normalize_timeout(-1), 5)
            self.assertEqual(module.JackettExtend._normalize_timeout(999), 120)
            self.assertEqual(module.JackettExtend._normalize_timeout("bad"), 30)
            plugin = object.__new__(module.JackettExtend)
            plugin._indexers_cache = []
            plugin._indexers_cache_ts = 1
            form, defaults = plugin.get_form()
            self.assertEqual(defaults["timeout"], 30)
            def contains_timeout(node):
                if isinstance(node, dict):
                    if node.get("props", {}).get("model") == "timeout":
                        return True
                    return any(contains_timeout(child) for child in node.get("content", []))
                if isinstance(node, list):
                    return any(contains_timeout(child) for child in node)
                return False
            self.assertTrue(contains_timeout(form))

    def test_diagnostic_endpoint_never_returns_credentials(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._host = "https://user:password@jackett.invalid/?apikey=secret"
            plugin._api_key = "secret"
            plugin._password = "password"
            plugin._enabled = True
            plugin._indexers = []
            plugin._authoritative_indexers = []
            plugin._fetch_ok = True
            plugin._sync_ready = False
            plugin._last_sync_ok = False
            plugin._last_sync_at = 0
            plugin._last_error = "timeout"
            plugin._last_error_at = 1
            payload = plugin.api_status()
            rendered = repr(payload)
            self.assertNotIn("secret", rendered)
            self.assertNotIn("password", rendered)
            self.assertNotIn("user:", rendered)
            self.assertNotIn("host", payload)
            self.assertEqual(payload["last_error"], "timeout")

    def test_parser_populates_v3_context_and_deduplicates_per_site(self):
        with loaded_module() as module:
            _RequestUtils.response = _Response()
            _RequestUtils.response.text = """<?xml version='1.0'?><rss><channel>
              <item><title>One</title><guid>same-guid</guid><comments>https://site/item/1</comments>
                <enclosure url='https://site/one.torrent'/><size>12</size>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='seeders' value='4'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='grabs' value='7'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='infohash' value='abc'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='label' value='trusted'/>
              </item>
              <item><title>Duplicate</title><guid>same-guid</guid><enclosure url='https://site/two.torrent'/></item>
              <item><title>Two</title><guid>unique-guid</guid><enclosure url='magnet:?xt=urn:btih:def'/></item>
            </channel></rss>"""
            plugin = object.__new__(module.JackettExtend)
            plugin._timeout = 12
            plugin._proxy = False
            plugin._last_error = None
            plugin._state_lock = module.JackettExtend._state_lock
            result = plugin._JackettExtend__parse_torznab_xml(
                "https://jackett.invalid/results?apikey=secret",
                site={"id": 3, "name": "Nyaa", "cookie": "cookie", "ua": "ua", "proxy": True, "pri": 2},
                mtype=_MediaType.MUSIC,
            )
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0].grabs, 7)
            self.assertEqual(result[0].site, 3)
            self.assertEqual(result[0].site_cookie, "cookie")
            self.assertEqual(result[0].site_order, 2)
            self.assertEqual(result[0].labels, ["trusted"])
            self.assertEqual(result[0].category, "音乐")
            self.assertEqual(_RequestUtils.timeouts[-1], 12)

    def test_empty_snapshot_cannot_trigger_destructive_cleanup(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._sync_stop_event = __import__("threading").Event()
            plugin._sync_generation = 1
            plugin._fetch_ok = False
            plugin._sync_ready = False
            plugin._authoritative_indexers = None
            plugin._indexers = []
            called = []

            def failed_status(generation=None):
                plugin._fetch_ok = True
                plugin._sync_ready = False
                plugin._authoritative_indexers = []
                plugin._indexers = []
                return False

            plugin.get_status = failed_status
            plugin._JackettExtend__cleanup_stale_selection = lambda *_args: called.append("selection")
            plugin._JackettExtend__sync_remove_stale_sites = lambda *_args: called.append("sites")
            plugin._JackettExtend__sync_all(generation=1)
            self.assertEqual(called, [])

    def test_old_generation_is_rejected_after_reload_event_replacement(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._sync_stop_event = __import__("threading").Event()
            plugin._sync_generation = 4

            self.assertFalse(plugin._sync_is_current(generation=3))
            self.assertTrue(plugin._sync_is_current(generation=4))

    def test_all_stale_selection_recomputes_empty_as_all_before_site_cleanup(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._sync_stop_event = __import__("threading").Event()
            plugin._sync_generation = 2
            plugin._fetch_ok = True
            plugin._sync_ready = True
            plugin._indexer_sites = ["removed"]
            authoritative = [
                {"indexer_id": "nyaa", "domain": "jackett_extend.nyaa"},
                {"indexer_id": "animetosho", "domain": "jackett_extend.animetosho"},
            ]
            plugin._authoritative_indexers = authoritative
            plugin._indexers = []
            plugin.sites_helper = None
            registered = []
            cleanup_snapshots = []

            def status(generation=None):
                return True

            def cleanup_selection(_snapshot, generation=None):
                plugin._indexer_sites = []

            plugin.get_status = status
            plugin._JackettExtend__cleanup_stale_selection = cleanup_selection
            plugin._JackettExtend__register_site = (
                lambda item, generation=None: registered.append(item["indexer_id"])
            )
            plugin._JackettExtend__sync_remove_stale_sites = (
                lambda snapshot, generation=None:
                cleanup_snapshots.append([item["indexer_id"] for item in snapshot])
            )

            plugin._JackettExtend__sync_all(generation=2)

            self.assertEqual(registered, ["nyaa", "animetosho"])
            self.assertEqual(cleanup_snapshots, [["nyaa", "animetosho"]])


if __name__ == "__main__":
    unittest.main()
