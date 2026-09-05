"""Current MoviePilot V3 contracts for the Prowlarr extension.

These tests deliberately load the plugin with small host shims.  They cover
the boundary owned by the entry point while the pure indexer/Torznab/UI
helpers are tested independently by their own contract tests.
"""

import asyncio
import codecs
import importlib.util
import json
import sys
import types
import unittest
import xml.dom.minidom
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.parsers.expat import ExpatError

import requests


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PATH = ROOT / "plugins.v3" / "prowlarrextend"


class _Logger:
    messages = []

    def __getattr__(self, _name):
        return lambda *args, **kwargs: self.messages.append(" ".join(map(str, args)))


class _MediaType:
    MUSIC = types.SimpleNamespace(value="音乐", name="MUSIC")


class _MediaSource:
    IMDb = "imdb"


class _TorrentInfo:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _StringUtils:
    clear_calls = []

    @staticmethod
    def clear(text, replace_word="", allow_space=False):
        _StringUtils.clear_calls.append((text, replace_word, allow_space))
        if text == "Test123, S!":
            return "Test123 S"
        if text == "落第賢者の学院無双 ~二度目の転生、Sランクチート魔術師冒険録~":
            return "落第賢者の学院無双 二度目の転生 Sランクチート魔術師冒険録"
        return text

    @staticmethod
    def unify_datetime_str(value):
        return value

    @staticmethod
    def get_url_domain(value):
        return value


class _CronTrigger:
    def __init__(self, expression, timezone=None):
        self.expression = expression
        self.timezone = timezone

    @classmethod
    def from_crontab(cls, expression, timezone=None):
        return cls(expression, timezone)


class _Response:
    def __init__(self, status_code=200, headers=None, text="", payload=None):
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "application/json"}
        self.text = text
        self.content = text.encode("utf-8")
        self._payload = payload

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        if self._payload is not None:
            return self._payload
        return json.loads(self.text)


def _real_xml_response(content):
    """Build a real requests.Response around an exact wire payload."""
    response = requests.Response()
    response.status_code = 200
    response.headers = {"Content-Type": "application/xml"}
    response._content = content
    response._content_consumed = True
    return response


@contextmanager
def loaded_module():
    names = {
        "apscheduler": types.ModuleType("apscheduler"),
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
    names["apscheduler.triggers.cron"].CronTrigger = _CronTrigger
    names["app.plugins"]._PluginBase = object
    names["app.schemas"].MediaType = _MediaType
    names["app.schemas"].__path__ = []
    names["app.schemas.types"].MediaSource = _MediaSource
    names["app.sdk"].__path__ = []
    names["app.sdk.config"].settings = types.SimpleNamespace(
        PROXY={"http": "http://proxy.invalid"}, TZ="UTC", USER_AGENT="test"
    )
    names["app.sdk.logging"].logger = _Logger()
    names["app.sdk.media"].TorrentInfo = _TorrentInfo
    names["app.sdk.network"].RequestUtils = object
    names["app.sdk.network"].SitesHelper = object
    names["app.sdk.utilities"].StringUtils = _StringUtils

    previous = {name: sys.modules.get(name) for name in names}
    package_name = "prowlarrextend_v3_test"
    previous_package = sys.modules.get(package_name)
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
        for name in list(sys.modules):
            if name.startswith(f"{package_name}."):
                sys.modules.pop(name, None)


@contextmanager
def site_oper_modules(site_oper, eventmanager, event_type):
    """Install isolated current V3 DB/event import shims."""
    names = [
        "app",
        "app.db",
        "app.db.oper",
        "app.db.oper.site",
        "app.sdk",
        "app.sdk.events",
        "app.schemas",
        "app.schemas.types",
    ]
    previous = {name: sys.modules.get(name) for name in names}
    parents = {
        name: sys.modules.get(name)
        for name in ("app", "app.db", "app.db.oper", "app.sdk", "app.schemas")
    }
    previous_attrs = {
        (name, "__path__"): getattr(module, "__path__", None)
        for name, module in parents.items()
        if module is not None
    }

    app = sys.modules.get("app") or types.ModuleType("app")
    app.__path__ = []
    db = types.ModuleType("app.db")
    db.__path__ = []
    oper = types.ModuleType("app.db.oper")
    oper.__path__ = []
    site = types.ModuleType("app.db.oper.site")
    site.SiteOper = site_oper
    sdk = sys.modules.get("app.sdk") or types.ModuleType("app.sdk")
    sdk.__path__ = []
    events = types.ModuleType("app.sdk.events")
    events.eventmanager = eventmanager
    schemas = sys.modules.get("app.schemas") or types.ModuleType("app.schemas")
    schemas.__path__ = []
    event_types = types.ModuleType("app.schemas.types")
    event_types.EventType = event_type
    sys.modules.update({
        "app": app,
        "app.db": db,
        "app.db.oper": oper,
        "app.db.oper.site": site,
        "app.sdk": sdk,
        "app.sdk.events": events,
        "app.schemas": schemas,
        "app.schemas.types": event_types,
    })

    try:
        yield
    finally:
        for name, previous_module in previous.items():
            if previous_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous_module
        for (parent_name, attr), value in previous_attrs.items():
            parent = sys.modules.get(parent_name)
            if parent is None:
                continue
            if value is None:
                try:
                    delattr(parent, attr)
                except AttributeError:
                    pass
            else:
                setattr(parent, attr, value)


class ProwlarrV3ContractTest(unittest.TestCase):
    def test_host_normalization_rejects_non_http_explicit_schemes(self):
        with loaded_module() as module:
            normalize = module.ProwlarrExtend._normalize_host
            self.assertEqual(normalize("prowlarr:9696"), "http://prowlarr:9696")
            self.assertEqual(normalize("https://prowlarr:9696/base/"), "https://prowlarr:9696/base")
            self.assertEqual(normalize("ftp://prowlarr:9696"), "")
            self.assertEqual(normalize("http://user:pass@prowlarr:9696"), "")

    def test_metadata_module_and_async_search_contract(self):
        manifest = json.loads((ROOT / "package.v3.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["ProwlarrExtend"]["version"], "1.0.5")
        self.assertEqual(manifest["ProwlarrExtend"]["icon"], "Prowlarr.png")
        self.assertEqual(manifest["ProwlarrExtend"]["author"], "oexi")
        self.assertEqual(manifest["JackettExtend"]["version"], "3.2.18")
        with loaded_module() as module:
            self.assertEqual(module.ProwlarrExtend.plugin_icon, "Prowlarr.png")
            self.assertEqual(module.ProwlarrExtend.plugin_author, "oexi")
            self.assertEqual(module.ProwlarrExtend.plugin_version, "1.0.5")
            self.assertEqual(module.ProwlarrExtend.plugin_config_prefix, "prowlarr_extend_")

            plugin = object.__new__(module.ProwlarrExtend)
            calls = []
            thread_calls = []

            def search(**kwargs):
                calls.append(kwargs)
                return ["ok"]

            async def fake_to_thread(func, *args, **kwargs):
                thread_calls.append(func)
                return func(*args, **kwargs)

            module.asyncio.to_thread = fake_to_thread
            plugin.search_torrents = search
            result = asyncio.run(plugin.async_search_torrents(
                site={"domain": "prowlarr_extend.7"}, keyword="x"
            ))

            self.assertEqual(result, ["ok"])
            self.assertEqual(thread_calls, [search])
            self.assertEqual(calls[0]["site"]["domain"], "prowlarr_extend.7")
            self.assertIn("async_search_torrents", plugin.get_module())

    def test_fetch_uses_read_only_v1_endpoint_header_auth_and_filters_resources(self):
        with loaded_module() as module:
            raw = [
                {
                    "id": 7,
                    "name": "Torrent Alpha",
                    "enable": True,
                    "protocol": "torrent",
                    "supportsSearch": True,
                    "privacy": "public",
                    "capabilities": {"categories": [{"id": 2000, "name": "Movies"}]},
                },
                {"id": 8, "name": "Disabled", "enable": False, "protocol": "torrent", "supportsSearch": True},
                {"id": 9, "name": "Usenet", "enable": True, "protocol": "usenet", "supportsSearch": True},
                {"id": 10, "name": "No search", "enable": True, "protocol": "torrent", "supportsSearch": False},
            ]
            response = _Response(
                headers={"Content-Type": "application/json; charset=utf-8"},
                text=json.dumps(raw),
                payload=raw,
            )
            calls = []

            class Request:
                def __init__(self, *args, **kwargs):
                    calls.append(("init", kwargs))

                def get_res(self, url, **kwargs):
                    calls.append(("get", url, kwargs))
                    return response

            module.RequestUtils = Request
            plugin = object.__new__(module.ProwlarrExtend)
            result = plugin._ProwlarrExtend__fetch_indexers({
                "host": "https://prowlarr.invalid/",
                "api_key": "not-a-real-key",
                "proxy": True,
                "timeout": 17,
            })

            self.assertEqual([item["indexer_id"] for item in result], ["7"])
            self.assertEqual(calls[1][1], "https://prowlarr.invalid/api/v1/indexer")
            self.assertEqual(calls[0][1]["timeout"], 17)
            self.assertEqual(calls[0][1]["headers"]["X-Api-Key"], "not-a-real-key")
            self.assertEqual(calls[1][2]["proxies"], module.settings.PROXY if hasattr(module, "settings") else {"http": "http://proxy.invalid"})
            self.assertTrue(calls[1][2]["raise_exception"])

    def test_fetch_http_json_empty_and_bounds_fail_closed(self):
        with loaded_module() as module:
            plugin = object.__new__(module.ProwlarrExtend)
            snapshot = {"host": "https://prowlarr.invalid", "api_key": "key", "timeout": 30, "proxy": False}

            class Request:
                response = None

                def __init__(self, *args, **kwargs):
                    pass

                def get_res(self, *args, **kwargs):
                    return self.response

            module.RequestUtils = Request
            cases = [
                _Response(status_code=401, text="{}", payload=[]),
                _Response(headers={"Content-Type": "text/plain"}, text="[]", payload=[]),
                _Response(headers={"Content-Type": "application/json"}, text="not-json", payload=ValueError()),
                _Response(headers={"Content-Type": "application/json"}, text="{}", payload={}),
                None,
            ]
            for response in cases:
                with self.subTest(response=response):
                    Request.response = response
                    self.assertIsNone(plugin._ProwlarrExtend__fetch_indexers(snapshot))

            class TimeoutRequest(Request):
                def get_res(self, *args, **kwargs):
                    import requests
                    raise requests.Timeout("network timeout")

            module.RequestUtils = TimeoutRequest
            self.assertIsNone(plugin._ProwlarrExtend__fetch_indexers(snapshot))
            self.assertEqual(plugin._last_error, "timeout")

            oversized = _Response(
                headers={"Content-Type": "application/json"},
                text="x" * (module.ProwlarrExtend.REST_MAX_JSON_BYTES + 1),
                payload=[],
            )
            Request.response = oversized
            self.assertIsNone(plugin._ProwlarrExtend__fetch_indexers(snapshot))

            plugin.REST_MAX_ITEMS = 1
            too_many = [{"id": 1}, {"id": 2}]
            Request.response = _Response(
                headers={"Content-Type": "application/json"},
                text=json.dumps(too_many),
                payload=too_many,
            )
            self.assertIsNone(plugin._ProwlarrExtend__fetch_indexers(snapshot))

    def test_search_uses_numeric_newznab_route_safe_query_and_v3_fields(self):
        with loaded_module() as module:
            xml = """<?xml version='1.0'?><rss><channel>
              <item><title>Release</title><guid>guid-1</guid>
                <comments>https://tracker.invalid/release</comments>
                <enclosure url='https://tracker.invalid/release.torrent'/><size>42</size>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='seeders' value='3'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='peers' value='4'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='infohash' value='abc'/>
              </item></channel></rss>"""
            response = _Response(
                headers={"Content-Type": "application/rss+xml"},
                text=xml,
            )
            calls = []

            class Request:
                def __init__(self, *args, **kwargs):
                    calls.append(("init", kwargs))

                def get_res(self, url, **kwargs):
                    calls.append(("get", url, kwargs))
                    return response

            module.RequestUtils = Request
            plugin = object.__new__(module.ProwlarrExtend)
            plugin._config_snapshot = {
                "host": "https://prowlarr.invalid",
                "api_key": "not-a-real-key",
                "proxy": False,
                "timeout": 12,
            }
            results = plugin.search_torrents(
                {"id": 4, "name": "Alpha", "domain": "prowlarr_extend.7"},
                keyword="A&B / secret",
                cat="2000, 5010",
            )
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "Release")
            self.assertEqual(results[0].size, 42.0)
            self.assertEqual(results[0].seeders, 3)
            self.assertEqual(results[0].site, 4)
            self.assertEqual(calls[1][1].split("?")[0], "https://prowlarr.invalid/api/v1/indexer/7/newznab")
            query = parse_qs(urlparse(calls[1][1]).query)
            self.assertEqual(query["q"], ["A&B / secret"])
            self.assertEqual(query["cat"], ["2000,5010"])
            self.assertNotIn("apikey", calls[1][1].lower())
            self.assertEqual(calls[0][1]["headers"]["X-Api-Key"], "not-a-real-key")
            self.assertTrue(calls[1][2]["raise_exception"])

            calls.clear()
            self.assertEqual(plugin.search_torrents(
                {"name": "bad", "domain": "prowlarr_extend.0"}, keyword="x"
            ), [])
            self.assertEqual(calls, [])

    def test_search_nfkc_normalizes_full_width_keyword_before_cleaning_and_query(self):
        _StringUtils.clear_calls.clear()
        with loaded_module() as module:
            calls = []

            class Request:
                def __init__(self, *args, **kwargs):
                    calls.append(("init", kwargs))

                def get_res(self, url, **kwargs):
                    calls.append(("get", url, kwargs))
                    return _Response(
                        headers={"Content-Type": "application/rss+xml"},
                        text="<?xml version='1.0'?><rss><channel /></rss>",
                    )

            module.RequestUtils = Request
            plugin = object.__new__(module.ProwlarrExtend)
            plugin._config_snapshot = {
                "host": "https://prowlarr.invalid",
                "api_key": "not-a-real-key",
                "proxy": False,
                "timeout": 12,
            }
            plugin.search_torrents(
                {"id": 4, "name": "Alpha", "domain": "prowlarr_extend.7"},
                keyword="Ｔｅｓｔ１２３， Ｓ！",
            )

            query = parse_qs(urlparse(calls[1][1]).query)
            self.assertEqual(query["q"], ["Test123 S"])
            self.assertEqual(
                _StringUtils.clear_calls,
                [("Test123, S!", " ", True)],
            )

            calls.clear()
            title = "落第賢者の学院無双 ～二度目の転生、Ｓランクチート魔術師冒険録～"
            expected = "落第賢者の学院無双 二度目の転生 Sランクチート魔術師冒険録"
            plugin.search_torrents(
                {"id": 4, "name": "Alpha", "domain": "prowlarr_extend.7"},
                keyword=title,
            )
            query = parse_qs(urlparse(calls[1][1]).query)
            self.assertEqual(query["q"], [expected])

    def test_search_keeps_normalized_chinese_and_ascii_keyword_unchanged(self):
        _StringUtils.clear_calls.clear()
        with loaded_module() as module:
            calls = []

            class Request:
                def __init__(self, *args, **kwargs):
                    pass

                def get_res(self, url, **kwargs):
                    calls.append(url)
                    return _Response(
                        headers={"Content-Type": "application/rss+xml"},
                        text="<?xml version='1.0'?><rss><channel /></rss>",
                    )

            module.RequestUtils = Request
            plugin = object.__new__(module.ProwlarrExtend)
            plugin._config_snapshot = {
                "host": "https://prowlarr.invalid",
                "api_key": "not-a-real-key",
                "proxy": False,
                "timeout": 12,
            }
            keywords = ("落第賢者の学院無双 Sランク", "ASCII 123")
            for keyword in keywords:
                plugin.search_torrents(
                    {"id": 4, "name": "Alpha", "domain": "prowlarr_extend.7"},
                    keyword=keyword,
                )

            self.assertEqual(
                [parse_qs(urlparse(url).query)["q"][0] for url in calls],
                list(keywords),
            )

    def test_yts_numeric_imdb_attr_reaches_moviepilot_identity_fast_path(self):
        with loaded_module() as module:
            fixture = (ROOT / "tests" / "fixtures" / "prowlarr_extend_yts.xml").read_text(
                encoding="utf-8"
            )
            response = _Response(
                headers={"Content-Type": "application/rss+xml"},
                text=fixture,
            )

            class Request:
                def __init__(self, *args, **kwargs):
                    pass

                def get_res(self, *_args, **_kwargs):
                    return response

            module.RequestUtils = Request
            plugin = object.__new__(module.ProwlarrExtend)
            plugin._config_snapshot = {
                "host": "https://prowlarr.invalid",
                "api_key": "not-a-real-key",
                "proxy": False,
                "timeout": 12,
            }

            results = plugin.search_torrents(
                {"id": 4, "name": "Sample Prowlarr", "domain": "prowlarr_extend.7"},
                keyword="Sample Film",
                mtype=types.SimpleNamespace(value="电影", name="MOVIE"),
            )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].media_source, module.MediaSource.IMDb)
            self.assertEqual(results[0].media_id, "tt0123456")
            self.assertEqual(results[0].category, "电影")
            self.assertEqual(results[0].size, 734003200.0)

            # Model the current MoviePilot SearchChain identity branch.  Keep
            # the title fallback deliberately false: a canonical IMDb ID must
            # still admit the release when title parsing cannot do so.
            target_media_source = module.MediaSource.IMDb
            target_media_id = "tt0123456"
            title_fallback_matches = "Different Target" in results[0].title
            identity_fast_path_matches = bool(
                results[0].media_source == target_media_source
                and target_media_id
                and results[0].media_id == target_media_id
            )
            self.assertFalse(title_fallback_matches)
            self.assertTrue(identity_fast_path_matches)

    def test_empty_site_browse_has_bounded_flaresolverr_budget(self):
        with loaded_module() as module:
            timeouts = []

            class Request:
                def __init__(self, *args, **kwargs):
                    timeouts.append(kwargs.get("timeout"))

                def get_res(self, *_args, **_kwargs):
                    return _Response(
                        headers={"Content-Type": "application/rss+xml"},
                        text="<?xml version='1.0'?><rss><channel /></rss>",
                    )

            module.RequestUtils = Request
            plugin = object.__new__(module.ProwlarrExtend)
            plugin._config_snapshot = {
                "host": "https://prowlarr.invalid",
                "api_key": "not-a-real-key",
                "proxy": False,
                "timeout": 12,
            }
            site = {"id": 4, "name": "Sample Prowlarr", "domain": "prowlarr_extend.7"}

            result = asyncio.run(plugin.async_refresh_torrents(site=site, keyword=None))

            self.assertEqual(result, [])
            self.assertEqual(timeouts, [module.ProwlarrExtend.BROWSE_TIMEOUT_MIN])

            timeouts.clear()
            plugin.search_torrents(site=site, keyword="Sample Film")
            self.assertEqual(timeouts, [12])

    def test_empty_refresh_propagates_sanitized_http_429_only(self):
        with loaded_module() as module:
            response = _Response(
                status_code=429,
                headers={
                    "Content-Type": "application/rss+xml",
                    "Retry-After": "sensitive-upstream-value",
                },
                text="<?xml version='1.0'?><rss><channel /></rss>",
            )

            class Request:
                def __init__(self, *args, **kwargs):
                    pass

                def get_res(self, *_args, **_kwargs):
                    return response

            module.RequestUtils = Request
            plugin = object.__new__(module.ProwlarrExtend)
            plugin._config_snapshot = {
                "host": "https://prowlarr.invalid",
                "api_key": "not-a-real-key",
                "proxy": False,
                "timeout": 9,
            }
            site = {"id": 4, "name": "Sample Prowlarr", "domain": "prowlarr_extend.7"}

            # Ordinary search stays fail-closed, including an empty keyword
            # supplied through the generic search route.
            self.assertEqual(plugin.search_torrents(site=site, keyword="title"), [])
            self.assertEqual(plugin.search_torrents(site=site, keyword=None), [])

            with self.assertRaises(module._host_compat.SanitizedUpstreamError) as raised:
                plugin.refresh_torrents(site=site, keyword=None)
            self.assertEqual(raised.exception.category, "http_429")
            self.assertNotIn("sensitive-upstream-value", str(raised.exception))

            with self.assertRaises(module._host_compat.SanitizedUpstreamError):
                asyncio.run(plugin.async_refresh_torrents(site=site, keyword=None))

    def test_torznab_http_json_xml_timeout_empty_and_limits_fail_closed(self):
        with loaded_module() as module:
            plugin = object.__new__(module.ProwlarrExtend)
            plugin._config_snapshot = {
                "host": "https://prowlarr.invalid",
                "api_key": "not-a-real-key",
                "proxy": False,
                "timeout": 9,
            }
            url = "https://prowlarr.invalid/api/v1/indexer/7/newznab?q=PrivateTitle"

            class Request:
                response = None

                def __init__(self, *args, **kwargs):
                    self.kwargs = kwargs

                def get_res(self, *_args, **_kwargs):
                    return self.response

            module.RequestUtils = Request
            cases = (
                None,
                _Response(status_code=401, headers={"Content-Type": "application/xml"}, text="<error/ >"),
                _Response(headers={"Content-Type": "application/json"}, text='{"error":"denied"}'),
                _Response(headers={"Content-Type": "application/xml"}, text=""),
                _Response(headers={"Content-Type": "application/xml"}, text="<rss>"),
                _Response(headers={"Content-Type": "application/xml"}, text='<error code="410" description="disabled"/>'),
                _Response(headers={"Content-Type": "application/xml"}, text="<!DOCTYPE rss><rss/ >"),
            )
            for response in cases:
                with self.subTest(response=response):
                    Request.response = response
                    self.assertEqual(plugin._ProwlarrExtend__parse_torznab_xml(url), [])

            Request.response = _Response(
                headers={"Content-Type": "application/xml"},
                text="<rss><channel>" + "x" * (plugin.TORZNAB_MAX_XML_BYTES + 1) + "</channel></rss>",
            )
            self.assertEqual(plugin._ProwlarrExtend__parse_torznab_xml(url), [])

            plugin.TORZNAB_MAX_ITEMS = 1
            Request.response = _Response(
                headers={"Content-Type": "application/xml"},
                text=("<rss><channel>"
                      "<item><title>One</title><enclosure url='https://site/one.torrent'/></item>"
                      "<item><title>Two</title><enclosure url='https://site/two.torrent'/></item>"
                      "</channel></rss>"),
            )
            self.assertEqual(plugin._ProwlarrExtend__parse_torznab_xml(url), [])

            class TimeoutRequest(Request):
                def get_res(self, *_args, **_kwargs):
                    import requests
                    raise requests.Timeout("sensitive transport detail")

            module.RequestUtils = TimeoutRequest
            self.assertEqual(plugin._ProwlarrExtend__parse_torznab_xml(url), [])
            self.assertEqual(plugin._last_search_error, "timeout")
            rendered_logs = "\n".join(_Logger.messages)
            self.assertNotIn("PrivateTitle", rendered_logs)
            self.assertNotIn("sensitive transport detail", rendered_logs)

    def test_torznab_uses_original_content_when_host_text_is_malformed(self):
        with loaded_module() as module:
            valid_xml = ("<?xml version='1.0'?><rss><channel>"
                         "<item><title>Wire Release</title>"
                         "<enclosure url='https://site/wire.torrent'/>"
                         "</item></channel></rss>").encode("utf-8")
            # Parsing the host-provided text would raise ExpatError, while
            # ``content`` contains the valid original response bytes.
            response = _Response(
                headers={"Content-Type": "application/rss+xml"},
                text="<rss>",
            )
            response.content = valid_xml
            with self.assertRaises(ExpatError):
                xml.dom.minidom.parseString(response.text)

            class Request:
                def __init__(self, *args, **kwargs):
                    pass

                def get_res(self, *_args, **_kwargs):
                    return response

            module.RequestUtils = Request
            plugin = object.__new__(module.ProwlarrExtend)
            plugin._config_snapshot = {
                "host": "https://prowlarr.invalid",
                "api_key": "not-a-real-key",
                "proxy": False,
                "timeout": 9,
            }

            results = plugin.search_torrents(
                {"id": 4, "name": "Wire Site", "domain": "prowlarr_extend.7"},
                keyword="Wire Release",
            )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "Wire Release")
            self.assertEqual(results[0].enclosure, "https://site/wire.torrent")

    def test_parser_rejects_dtd_in_all_supported_wire_encodings(self):
        with loaded_module() as module:
            plugin = object.__new__(module.ProwlarrExtend)
            plugin._config_snapshot = {
                "host": "https://prowlarr.invalid",
                "api_key": "not-a-real-key",
                "proxy": False,
                "timeout": 9,
            }
            plugin._last_error = None
            plugin._state_lock = module.ProwlarrExtend._state_lock

            class Request:
                response = None

                def __init__(self, *args, **kwargs):
                    pass

                def get_res(self, *_args, **_kwargs):
                    return self.response

            module.RequestUtils = Request
            encodings = (
                ("utf-8", "UTF-8", codecs.BOM_UTF8),
                ("utf-16-le", "UTF-16", codecs.BOM_UTF16_LE),
                ("utf-16-be", "UTF-16", codecs.BOM_UTF16_BE),
                ("utf-32-le", "UTF-32", codecs.BOM_UTF32_LE),
                ("utf-32-be", "UTF-32", codecs.BOM_UTF32_BE),
            )
            for encoding, declaration, bom in encodings:
                for with_declaration in (False, True):
                    for with_bom in (False, True):
                        with self.subTest(
                                encoding=encoding,
                                with_declaration=with_declaration,
                                with_bom=with_bom):
                            xml = (
                                f'<?xml version="1.0" encoding="{declaration}"?>'
                                if with_declaration else ""
                            ) + (
                                '<!DOCTYPE rss [<!ENTITY x "expanded">]>'
                                '<rss><channel><item><title>&x;</title>'
                                '<enclosure url="https://site/release.torrent"/>'
                                '</item></channel></rss>'
                            )
                            content = xml.encode(encoding)
                            if with_bom:
                                content = bom + content
                            response = _real_xml_response(content)
                            self.assertIsInstance(response, requests.Response)
                            Request.response = response
                            plugin._last_search_error = None

                            self.assertEqual(
                                plugin._ProwlarrExtend__parse_torznab_xml(
                                    "https://prowlarr.invalid/results?q=private-title&apikey=secret"
                                ),
                                [],
                            )
                            self.assertEqual(plugin._last_search_error, "xml_doctype")

    def test_parser_preserves_xml_declaration_encoding_for_real_response(self):
        with loaded_module() as module:
            plugin = object.__new__(module.ProwlarrExtend)
            plugin._config_snapshot = {
                "host": "https://prowlarr.invalid",
                "api_key": "not-a-real-key",
                "proxy": False,
                "timeout": 9,
            }
            plugin._last_error = None
            plugin._state_lock = module.ProwlarrExtend._state_lock

            class Request:
                response = None

                def __init__(self, *args, **kwargs):
                    pass

                def get_res(self, *_args, **_kwargs):
                    return self.response

            module.RequestUtils = Request
            cases = (
                ("utf-8", "UTF-8", "中文标题"),
                ("utf-16-le", "UTF-16", "日本語タイトル"),
                ("utf-16-be", "UTF-16", "中文と日本語"),
                ("iso-8859-1", "ISO-8859-1", "Café release"),
            )
            for encoding, declaration, title in cases:
                with self.subTest(encoding=encoding):
                    xml = (
                        f'<?xml version="1.0" encoding="{declaration}"?><rss><channel>'
                        f'<item><title>{title}</title>'
                        '<enclosure url="https://site/release.torrent"/>'
                        '</item></channel></rss>'
                    )
                    Request.response = _real_xml_response(xml.encode(encoding))
                    plugin._last_search_error = None

                    result = plugin._ProwlarrExtend__parse_torznab_xml(
                        "https://prowlarr.invalid/results",
                        site={"name": "Encoding Site"},
                    )

                    self.assertEqual(len(result), 1)
                    self.assertEqual(result[0].title, title)
                    self.assertIsNone(plugin._last_search_error)

    def test_service_config_and_diagnostics_are_current_v3_contracts(self):
        with loaded_module() as module:
            plugin = object.__new__(module.ProwlarrExtend)
            plugin._enabled = True
            plugin._cron = "*/15 * * * *"
            plugin._sync_generation = 3
            plugin._ProwlarrExtend__sync_all = lambda generation=None: generation
            service = plugin.get_service()[0]
            self.assertEqual(service["id"], "prowlarr_extend_sync")
            self.assertEqual(service["func_kwargs"], {"generation": 3})
            self.assertEqual(service["kwargs"]["max_instances"], 1)
            self.assertEqual(service["kwargs"]["coalesce"], True)

            plugin._host = "https://user:secret@prowlarr.invalid"
            plugin._api_key = "not-a-real-key"
            plugin._indexers = []
            plugin._authoritative_indexers = []
            plugin._fetch_ok = False
            plugin._sync_ready = False
            plugin._last_sync_ok = False
            plugin._last_error = "timeout"
            plugin._last_error_at = 1
            payload = plugin.api_status()
            rendered = repr(payload)
            self.assertNotIn("not-a-real-key", rendered)
            self.assertNotIn("user:", rendered)
            self.assertNotIn("host", payload)
            self.assertEqual(payload["last_error"], "timeout")

            plugin._enabled = False
            self.assertEqual(plugin.get_service(), [])

    def test_lifecycle_cleanup_deletes_only_prowlarr_sites_and_is_idempotent(self):
        with loaded_module() as module:
            records = [
                types.SimpleNamespace(id=1, domain="prowlarr_extend.7"),
                types.SimpleNamespace(id=2, domain="ordinary.example"),
                types.SimpleNamespace(id=3, domain="jackett_extend.old"),
            ]
            state = types.SimpleNamespace(deleted=[])

            class FakeSiteOper:
                def list(self):
                    return [record for record in records if record.id not in state.deleted]

                def delete(self, site_id):
                    state.deleted.append(site_id)

            calls = []

            class EventManager:
                def send_event(self, event, payload):
                    calls.append((event, payload))

            event_type = types.SimpleNamespace(SiteDeleted="SiteDeleted")
            plugin = object.__new__(module.ProwlarrExtend)

            with site_oper_modules(FakeSiteOper, EventManager(), event_type):
                self.assertTrue(plugin._ProwlarrExtend__remove_managed_sites())
                self.assertTrue(plugin._ProwlarrExtend__remove_managed_sites())

            self.assertEqual(state.deleted, [1])
            self.assertEqual(calls, [("SiteDeleted", {"site_id": 1})])

    def test_lifecycle_cleanup_contains_row_and_event_failures(self):
        with loaded_module() as module:
            records = [
                types.SimpleNamespace(id=1, domain="prowlarr_extend.7"),
                types.SimpleNamespace(id=2, domain="prowlarr_extend.8"),
                types.SimpleNamespace(id=3, domain="prowlarr_extend.9"),
            ]
            state = types.SimpleNamespace(deleted=[])

            class FakeSiteOper:
                def list(self):
                    return records

                def delete(self, site_id):
                    if site_id == 2:
                        raise RuntimeError("delete unavailable")
                    state.deleted.append(site_id)

            calls = []

            class EventManager:
                def send_event(self, event, payload):
                    calls.append((event, payload))
                    if payload["site_id"] == 3:
                        raise RuntimeError("event unavailable")

            event_type = types.SimpleNamespace(SiteDeleted="SiteDeleted")
            plugin = object.__new__(module.ProwlarrExtend)

            with site_oper_modules(FakeSiteOper, EventManager(), event_type):
                result = plugin._ProwlarrExtend__remove_managed_sites()

            self.assertFalse(result)
            self.assertEqual(state.deleted, [1, 3])
            self.assertEqual(calls, [
                ("SiteDeleted", {"site_id": 1}),
                ("SiteDeleted", {"site_id": 3}),
            ])

    def test_disabled_init_cleans_sites_without_network_sync(self):
        with loaded_module() as module:
            records = [
                types.SimpleNamespace(id=1, domain="prowlarr_extend.7"),
                types.SimpleNamespace(id=2, domain="ordinary.example"),
            ]
            state = types.SimpleNamespace(deleted=[])

            class FakeSiteOper:
                def list(self):
                    return [record for record in records if record.id not in state.deleted]

                def delete(self, site_id):
                    state.deleted.append(site_id)

            calls = []

            class EventManager:
                def send_event(self, event, payload):
                    calls.append((event, payload))

            event_type = types.SimpleNamespace(SiteDeleted="SiteDeleted")
            plugin = object.__new__(module.ProwlarrExtend)

            with site_oper_modules(FakeSiteOper, EventManager(), event_type):
                plugin.init_plugin({"enabled": False})

            self.assertFalse(plugin.get_state())
            self.assertEqual(state.deleted, [1])
            self.assertEqual(calls, [
                ("SiteDeleted", {"site_id": 1}),
            ])

    def test_stop_service_cleans_sites_and_marks_plugin_disabled(self):
        with loaded_module() as module:
            records = [types.SimpleNamespace(id=1, domain="prowlarr_extend.7")]
            state = types.SimpleNamespace(deleted=[])

            class FakeSiteOper:
                def list(self):
                    return [record for record in records if record.id not in state.deleted]

                def delete(self, site_id):
                    state.deleted.append(site_id)

            calls = []

            class EventManager:
                def send_event(self, event, payload):
                    calls.append((event, payload))

            event_type = types.SimpleNamespace(SiteDeleted="SiteDeleted")
            plugin = object.__new__(module.ProwlarrExtend)
            plugin._enabled = True

            with site_oper_modules(FakeSiteOper, EventManager(), event_type):
                result = plugin.stop_service()

            self.assertTrue(result)
            self.assertFalse(plugin.get_state())
            self.assertEqual(state.deleted, [1])
            self.assertEqual(calls, [
                ("SiteDeleted", {"site_id": 1}),
            ])


if __name__ == "__main__":
    unittest.main()
