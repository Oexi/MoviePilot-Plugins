import asyncio
import copy
import importlib.util
import inspect
import json
import sys
import threading
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
        for name in list(sys.modules):
            if name.startswith(f"{package_name}."):
                sys.modules.pop(name, None)


@contextmanager
def isolated_ui_module():
    """Load the pure UI builder without installing MoviePilot shims."""
    package_name = "jackettextend_ui_test"
    module_name = f"{package_name}._ui"
    previous_package = sys.modules.get(package_name)
    previous_submodules = {
        name: module
        for name, module in sys.modules.items()
        if name.startswith(f"{package_name}.")
    }
    package = types.ModuleType(package_name)
    package.__path__ = [str(PACKAGE_PATH)]
    sys.modules[package_name] = package
    try:
        spec = importlib.util.spec_from_file_location(
            module_name,
            PACKAGE_PATH / "_ui.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        for name in list(sys.modules):
            if name.startswith(f"{package_name}."):
                sys.modules.pop(name, None)
        sys.modules.update(previous_submodules)
        if previous_package is None:
            sys.modules.pop(package_name, None)
        else:
            sys.modules[package_name] = previous_package


@contextmanager
def site_oper_modules(site_oper, eventmanager=None, event_type=None, fallback=False):
    """Install isolated DB/event import shims for the real sync boundaries."""
    names = [
        "app",
        "app.db",
        "app.db.site_oper",
        "app.db.oper",
        "app.db.oper.site",
        "app.sdk",
        "app.sdk.events",
        "app.schemas",
        "app.schemas.types",
    ]
    previous = {name: sys.modules.get(name) for name in names}
    previous_attrs = {}
    for parent_name in ("app", "app.db", "app.db.oper", "app.sdk", "app.schemas"):
        parent = sys.modules.get(parent_name)
        if parent is not None:
            previous_attrs[(parent_name, "__path__")] = getattr(parent, "__path__", None)

    app = sys.modules.get("app") or types.ModuleType("app")
    app.__path__ = []
    db = types.ModuleType("app.db")
    db.__path__ = []
    sdk = sys.modules.get("app.sdk") or types.ModuleType("app.sdk")
    sdk.__path__ = []
    schemas = sys.modules.get("app.schemas") or types.ModuleType("app.schemas")
    schemas.__path__ = []
    sys.modules.update({"app": app, "app.db": db, "app.sdk": sdk, "app.schemas": schemas})

    if fallback:
        oper = types.ModuleType("app.db.oper")
        oper.__path__ = []
        site = types.ModuleType("app.db.oper.site")
        site.SiteOper = site_oper
        sys.modules.update({"app.db.oper": oper, "app.db.oper.site": site})
    else:
        primary = types.ModuleType("app.db.site_oper")
        primary.SiteOper = site_oper
        sys.modules["app.db.site_oper"] = primary

    if eventmanager is not None:
        events = types.ModuleType("app.sdk.events")
        events.eventmanager = eventmanager
        event_types = types.ModuleType("app.schemas.types")
        event_types.EventType = event_type
        sys.modules.update({"app.sdk.events": events, "app.schemas.types": event_types})

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


class JackettV3ContractTest(unittest.TestCase):
    @staticmethod
    def _page_texts(page):
        values = []

        def walk(node):
            if isinstance(node, dict):
                if "text" in node:
                    values.append(node["text"])
                for child in node.get("content", []):
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(page)
        return values

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

    def test_get_service_exposes_shared_scheduler_contract(self):
        with loaded_module() as module:
            class FakeCronTrigger:
                def __init__(self, expression, timezone=None):
                    self.expression = expression
                    self.timezone = timezone

                @classmethod
                def from_crontab(cls, expression, timezone=None):
                    return cls(expression, timezone=timezone)

            module.CronTrigger = FakeCronTrigger
            plugin = object.__new__(module.JackettExtend)
            plugin._enabled = True
            plugin._cron = "*/15 * * * *"
            plugin._sync_generation = 4
            calls = []

            def sync_all(generation=None):
                calls.append(generation)

            plugin._JackettExtend__sync_all = sync_all

            services = plugin.get_service()

            self.assertIsInstance(services, list)
            self.assertEqual(len(services), 1)
            service = services[0]
            self.assertTrue(service["id"])
            self.assertEqual(service["id"], plugin.get_service()[0]["id"])
            self.assertTrue(service["name"])
            self.assertIsInstance(service["trigger"], FakeCronTrigger)
            self.assertEqual(service["trigger"].expression, "*/15 * * * *")
            self.assertEqual(service["trigger"].timezone, "UTC")
            self.assertTrue(callable(service["func"]))
            self.assertEqual(service["func_kwargs"], {"generation": 4})
            self.assertEqual(service["kwargs"], {
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 3600,
            })
            service["func"](**service["func_kwargs"])
            self.assertEqual(calls, [4])

            plugin._enabled = False
            self.assertEqual(plugin.get_service(), [])

    def test_timeout_is_bounded_and_written_to_form_defaults(self):
        with loaded_module() as module:
            self.assertEqual(module.JackettExtend._normalize_timeout(-1), 5)
            self.assertEqual(module.JackettExtend._normalize_timeout(999), 120)
            self.assertEqual(module.JackettExtend._normalize_timeout("bad"), 30)
            for value in (float("inf"), float("-inf"), float("nan")):
                self.assertEqual(module.JackettExtend._normalize_timeout(value), 30)
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

    def test_get_page_renders_privacy_types_and_public_fallback(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._indexers = [
                {"id": "public", "domain": "public", "privacy": "public", "public": True},
                {"id": "semi", "domain": "semi", "privacy": "semi-private", "public": False},
                {"id": "private", "domain": "private", "privacy": "private", "public": False},
                # A profile without new metadata uses the established public
                # boolean as a coarse fallback.
                {"id": "fallback-public", "domain": "fallback-public", "public": True},
                {"id": "fallback-private", "domain": "fallback-private", "public": False},
                {"id": "unknown", "domain": "unknown", "privacy": "unknown", "public": False},
                # Invalid legacy data remains visibly unknown rather than
                # being silently treated as private.
                {"id": "malformed", "domain": "malformed", "public": "false"},
            ]

            page = plugin.get_page()
            texts = self._page_texts(page)
            self.assertIn("类型", texts)
            for label in ("公开", "半公开", "私有", "未知"):
                self.assertIn(label, texts)
            self.assertNotIn("True", texts)
            self.assertNotIn("False", texts)

    def test_get_form_preserves_configuration_models_options_and_description(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin.get_indexers = lambda filter_selected=False: [
                {"name": "Nyaa", "indexer_id": "nyaa"},
            ]

            form, defaults = plugin.get_form()

            self.assertEqual(defaults, {
                "enabled": False,
                "proxy": False,
                "host": "",
                "api_key": "",
                "password": "",
                "cron": "0 0 * * *",
                "timeout": 30,
                "indexer_sites": [],
            })

            fields = {}
            alerts = []

            def collect(node):
                if isinstance(node, dict):
                    props = node.get("props", {})
                    model = props.get("model")
                    if model:
                        fields[model] = props
                    if node.get("component") == "VAlert":
                        alerts.append(props)
                    for child in node.get("content", []):
                        collect(child)
                elif isinstance(node, list):
                    for child in node:
                        collect(child)

            collect(form)
            self.assertEqual(
                set(fields),
                {"enabled", "timeout", "proxy", "host", "api_key", "password", "cron", "indexer_sites"},
            )
            self.assertEqual(fields["enabled"]["label"], "启用插件")
            self.assertEqual(fields["timeout"]["placeholder"], "30")
            self.assertIn("范围 5-120 秒", fields["timeout"]["hint"])
            self.assertEqual(fields["host"]["placeholder"], "http://127.0.0.1:9117")
            self.assertEqual(fields["api_key"]["label"], "Api Key")
            self.assertEqual(fields["password"]["type"], "password")
            self.assertEqual(fields["cron"]["placeholder"], "0 0 * * *")
            self.assertEqual(fields["indexer_sites"]["items"], [
                {"title": "Nyaa (nyaa)", "value": "nyaa"},
            ])
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["type"], "info")
            self.assertEqual(alerts[0]["variant"], "tonal")
            self.assertEqual(
                alerts[0]["text"],
                "该方式通过 Jackett Torznab API 扩展检索，站点由插件自动注册到站点列表，"
                "并随定时任务与白名单配置自动同步新增、更新与移除。"
                "如遇网络或 API 错误，请查看日志确认 Jackett 地址、Api Key 与密码配置正确。",
            )

    def test_get_page_preserves_table_rows_and_empty_load_behavior(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._indexers = [{
                "id": "JackettExtend-Nyaa",
                "domain": "jackett_extend.nyaa",
                "privacy": "semi-private",
                "public": False,
            }]

            page = plugin.get_page()
            table = page[0]["content"][0]["content"][0]
            header = table["content"][0]["content"][0]["content"]
            row = table["content"][1]["content"][0]
            self.assertEqual([cell["text"] for cell in header], ["id", "站点domain", "类型"])
            self.assertEqual(
                [cell["text"] for cell in row["content"]],
                ["JackettExtend-Nyaa", "https://jackett_extend.nyaa/", "半公开"],
            )

            plugin._ensure_sites_loaded = lambda: False
            self.assertEqual(plugin.get_page(), [])

    def test_ui_builders_are_host_independent_and_do_not_mutate_inputs(self):
        with isolated_ui_module() as ui:
            options = [{"title": "Nyaa (nyaa)", "value": "nyaa"}]
            options_before = copy.deepcopy(options)
            form, defaults = ui.build_form(options, timeout_default=30, timeout_min=5, timeout_max=120)

            self.assertEqual(options, options_before)
            self.assertEqual(defaults["timeout"], 30)
            self.assertEqual(
                form[0]["content"][2]["content"][2]["content"][0]["props"]["items"],
                options_before,
            )

            indexers = [{
                "id": "JackettExtend-Nyaa",
                "domain": "jackett_extend.nyaa",
                "privacy": "public",
                "public": True,
            }]
            indexers_before = copy.deepcopy(indexers)
            page = ui.build_page(indexers)
            self.assertEqual(indexers, indexers_before)
            self.assertEqual(
                page[0]["content"][0]["content"][0]["content"][1]["content"][0]["content"][1]["text"],
                "https://jackett_extend.nyaa/",
            )

    def test_keyword_mask_does_not_retain_original_prefix(self):
        with loaded_module() as module:
            masked = module.JackettExtend._JackettExtend__mask_keyword("Secret Title")
            self.assertNotIn("Secret", masked)
            self.assertNotIn("Title", masked)
            self.assertIn("(12)", masked)

    def test_whitelist_serializations_become_canonical_ids(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            for raw in (
                ["Nyaa", " AnimeTosho "],
                "Nyaa, AnimeTosho",
                "['Nyaa', 'AnimeTosho']",
                ["['Nyaa'", "'AnimeTosho']"],
                "['[\\\"Nyaa\\\", \\\"AnimeTosho\\\"]']",
            ):
                plugin._indexer_sites = raw
                self.assertEqual(plugin._parse_indexer_sites(), ["nyaa", "animetosho"])

            plugin.stop_service = lambda: None
            plugin.init_plugin({
                "enabled": False,
                "indexer_sites": "['Nyaa', 'AnimeTosho']",
            })
            self.assertEqual(plugin._indexer_sites, ["nyaa", "animetosho"])
            self.assertTrue(plugin._indexer_sites_explicit)

    def test_indexer_profiles_filter_bad_rows_and_encode_special_ids(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)

            class Response:
                status_code = 200
                headers = {"Content-Type": "application/json"}
                text = "[]"

                @staticmethod
                def json():
                    return [
                        None,
                        {"id": "foo.bar/baz", "name": "Special", "type": "public", "caps": []},
                    ]

            class Request:
                def __init__(self, *args, **kwargs):
                    self.kwargs = kwargs

                def get_res(self, *_args, **_kwargs):
                    return Response()

            original = module.RequestUtils
            module.RequestUtils = Request
            try:
                result = plugin._JackettExtend__fetch_indexers({
                    "host": "https://jackett.invalid",
                    "api_key": "key",
                    "password": "",
                    "proxy": False,
                    "timeout": 30,
                })
            finally:
                module.RequestUtils = original
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["indexer_id"], "foo.bar/baz")
            self.assertEqual(result[0]["privacy"], "public")
            self.assertTrue(result[0]["public"])
            self.assertIn("foo.bar%2Fbaz", result[0]["url"])
            self.assertIn("foo.bar%2Fbaz", result[0]["domain"])

    def test_finite_all_stale_whitelist_never_becomes_all(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._indexer_sites = ["removed"]
            plugin._indexer_sites_explicit = True
            selected = plugin._apply_indexer_selection([
                {"indexer_id": "nyaa", "domain": "jackett_extend.nyaa"},
            ])
            self.assertEqual(selected, [])

    def test_sync_captures_generation_config_snapshot_before_status(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._sync_stop_event = __import__("threading").Event()
            plugin._sync_generation = 7
            plugin._config_snapshot = {
                "host": "https://old.invalid",
                "api_key": "old-key",
                "password": "old-password",
                "proxy": True,
                "timeout": 17,
            }
            captured = {}

            def status(generation=None, config_snapshot=None):
                captured.update(config_snapshot or {})
                return False

            plugin.get_status = status
            plugin._JackettExtend__sync_all(generation=7)
            self.assertEqual(captured["host"], "https://old.invalid")
            self.assertEqual(captured["api_key"], "old-key")
            self.assertEqual(captured["password"], "old-password")
            self.assertTrue(captured["proxy"])
            self.assertEqual(captured["timeout"], 17)

    def test_stale_generation_cannot_commit_cache_state_or_side_effects(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._sync_stop_event = threading.Event()
            plugin._sync_generation = 11
            plugin._config_snapshot = {
                "host": "https://old.invalid",
                "api_key": "old-key",
                "password": "old-password",
                "proxy": False,
                "timeout": 17,
            }
            cached = [{"indexer_id": "cached"}]
            authoritative = [{"indexer_id": "current"}]
            selected = [{"indexer_id": "selected"}]
            plugin._indexers_cache = cached
            plugin._indexers_cache_ts = 123.0
            plugin._authoritative_indexers = authoritative
            plugin._indexers = selected
            plugin._fetch_ok = True
            plugin._sync_ready = True
            plugin._last_sync_ok = True
            plugin._indexer_sites = []
            entered = threading.Event()
            release = threading.Event()
            side_effects = []

            def delayed_fetch(config_snapshot=None, generation=None):
                entered.set()
                self.assertTrue(release.wait(2))
                return [{
                    "indexer_id": "stale-result",
                    "domain": "jackett_extend.stale-result",
                }]

            plugin._JackettExtend__fetch_indexers = delayed_fetch
            plugin._JackettExtend__register_site = (
                lambda *_args, **_kwargs: side_effects.append("register")
            )
            plugin._JackettExtend__sync_remove_stale_sites = (
                lambda *_args, **_kwargs: side_effects.append("cleanup")
            )
            worker = threading.Thread(
                target=plugin._JackettExtend__sync_all,
                kwargs={"generation": 11},
            )
            worker.start()
            self.assertTrue(entered.wait(2))
            with plugin._state_lock:
                plugin._sync_generation = 12
            release.set()
            worker.join(2)

            self.assertFalse(worker.is_alive())
            self.assertIs(plugin._indexers_cache, cached)
            self.assertEqual(plugin._indexers_cache_ts, 123.0)
            self.assertIs(plugin._authoritative_indexers, authoritative)
            self.assertIs(plugin._indexers, selected)
            self.assertTrue(plugin._last_sync_ok)
            self.assertEqual(side_effects, [])

    def test_none_config_clears_previous_credentials_and_defaults(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin.stop_service = lambda: None
            plugin._host = "https://old.invalid"
            plugin._api_key = "old-key"
            plugin._password = "old-password"
            plugin._enabled = True
            plugin._proxy = True
            plugin._timeout = 99
            plugin._indexer_sites = ["nyaa"]
            plugin.init_plugin(None)
            self.assertEqual(plugin._host, "")
            self.assertEqual(plugin._api_key, "")
            self.assertEqual(plugin._password, "")
            self.assertFalse(plugin._enabled)
            self.assertFalse(plugin._proxy)
            self.assertEqual(plugin._timeout, 30)
            self.assertEqual(plugin._indexer_sites, [])

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

    def test_probe_failure_keeps_sync_error_and_reports_local_probe_error(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._host = ""
            plugin._api_key = ""
            plugin._password = ""
            plugin._enabled = True
            plugin._last_error = "sync_timeout"
            plugin._last_error_at = 11
            plugin._indexers = []
            plugin._authoritative_indexers = []
            plugin._fetch_ok = True
            plugin._sync_ready = True
            plugin._last_sync_ok = True
            plugin._last_sync_at = 10

            payload = plugin.api_test()

            self.assertFalse(payload["ok"])
            self.assertFalse(payload["connected"])
            self.assertEqual(payload["last_error"], "sync_timeout")
            self.assertEqual(payload["last_error_at"], 11)
            self.assertEqual(payload["probe_error"], "missing_config")
            self.assertIsNotNone(payload["probe_error_at"])

    def test_probe_success_keeps_sync_error_and_reports_success(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._host = "https://jackett.invalid"
            plugin._api_key = "key"
            plugin._password = ""
            plugin._enabled = True
            plugin._last_error = "sync_timeout"
            plugin._last_error_at = 11
            plugin._indexers = []
            plugin._authoritative_indexers = []
            plugin._fetch_ok = False
            plugin._sync_ready = False
            plugin._last_sync_ok = False
            plugin._last_sync_at = 10

            def fetch_probe(**_kwargs):
                return [{"indexer_id": "nyaa", "domain": "jackett_extend.nyaa"}]

            plugin._JackettExtend__fetch_indexers = fetch_probe
            payload = plugin.api_test()

            self.assertTrue(payload["ok"])
            self.assertTrue(payload["connected"])
            self.assertEqual(payload["last_error"], "sync_timeout")
            self.assertEqual(payload["last_error_at"], 11)
            self.assertIsNone(payload["probe_error"])
            self.assertIsNone(payload["probe_error_at"])

    def test_search_error_isolated_from_sync_error_and_exposed_in_status(self):
        with loaded_module() as module:
            _RequestUtils.response = _Response()
            _RequestUtils.response.text = (
                '<?xml version="1.0"?><error code="100" '
                'description="secret diagnostic"/>'
            )
            plugin = object.__new__(module.JackettExtend)
            plugin._host = "https://jackett.invalid"
            plugin._api_key = "key"
            plugin._timeout = 12
            plugin._proxy = False
            plugin._last_error = "sync_timeout"
            plugin._last_error_at = 11
            plugin._state_lock = module.JackettExtend._state_lock

            result = plugin._JackettExtend__parse_torznab_xml(
                "https://jackett.invalid/results?q=secret&apikey=key",
            )
            payload = plugin.api_status()

            self.assertEqual(result, [])
            self.assertEqual(plugin._last_error, "sync_timeout")
            self.assertEqual(plugin._last_search_error, "torznab_error")
            self.assertEqual(payload["last_error"], "sync_timeout")
            self.assertEqual(payload["last_search_error"], "torznab_error")
            rendered = repr(payload)
            self.assertNotIn("secret", rendered)
            self.assertNotIn("diagnostic", rendered)
            self.assertNotIn("key", rendered)

    def test_parser_populates_v3_context_and_deduplicates_per_site(self):
        with loaded_module() as module:
            _RequestUtils.response = _Response()
            _RequestUtils.response.text = """<?xml version='1.0'?><rss><channel>
              <item><title>One</title><guid>same-guid</guid><comments>https://site/item/1</comments>
                <enclosure url='https://site/one.torrent'/><size>12</size>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='seeders' value='4'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='peers' value='5'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='grabs' value='7'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='uploadvolumefactor' value='2.5'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='downloadvolumefactor' value='0'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='infohash' value='abc'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='label' value='trusted'/>
              </item>
              <item><title>Different infohash, same GUID</title><guid>same-guid</guid><enclosure url='https://site/two.torrent'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='infohash' value='different-hash'/>
              </item>
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
            self.assertEqual(len(result), 3)
            self.assertEqual(result[0].size, 12.0)
            self.assertEqual(result[0].seeders, 4)
            self.assertEqual(result[0].peers, 5)
            self.assertEqual(result[0].grabs, 7)
            self.assertEqual(result[0].uploadvolumefactor, 2.5)
            self.assertEqual(result[0].downloadvolumefactor, 0.0)
            self.assertEqual(result[0].site, 3)
            self.assertEqual(result[0].site_cookie, "cookie")
            self.assertEqual(result[0].site_order, 2)
            self.assertEqual(result[0].labels, ["trusted"])
            self.assertEqual(result[0].category, "音乐")
            self.assertEqual(_RequestUtils.timeouts[-1], 12)

    def test_parser_normalizes_valid_imdb_identity(self):
        with loaded_module() as module:
            _RequestUtils.response = _Response()
            _RequestUtils.response.text = """<?xml version='1.0'?><rss><channel>
              <item><title>IMDb result</title><enclosure url='https://site/imdb.torrent'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='imdbid' value=' TT1234567 '/>
              </item>
            </channel></rss>"""
            plugin = object.__new__(module.JackettExtend)
            plugin._timeout = 12
            plugin._proxy = False
            plugin._last_error = None
            plugin._state_lock = module.JackettExtend._state_lock

            result = plugin._JackettExtend__parse_torznab_xml(
                "https://jackett.invalid/results",
                site={"name": "Nyaa"},
            )

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].media_source, _MediaSource.IMDb)
            self.assertEqual(result[0].media_id, "tt1234567")

    def test_parser_accepts_imdb_identity_without_maximum_digit_count(self):
        with loaded_module() as module:
            _RequestUtils.response = _Response()
            _RequestUtils.response.text = """<?xml version='1.0'?><rss><channel>
              <item><title>Long IMDb result</title><enclosure url='https://site/long-imdb.torrent'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='imdbid' value='tt12345678901234567890'/>
              </item>
            </channel></rss>"""
            plugin = object.__new__(module.JackettExtend)
            plugin._timeout = 12
            plugin._proxy = False
            plugin._last_error = None
            plugin._state_lock = module.JackettExtend._state_lock

            result = plugin._JackettExtend__parse_torznab_xml(
                "https://jackett.invalid/results",
                site={"name": "Nyaa"},
            )

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].media_source, _MediaSource.IMDb)
            self.assertEqual(result[0].media_id, "tt12345678901234567890")

    def test_parser_keeps_torrents_with_invalid_imdb_identity_unset(self):
        invalid_values = (
            "",
            "1234567",
            "tt123456",
            "TT0000000000",
            "tt123456x",
            "tt1234567!",
            "tt１２３４５６７",
        )
        items = "".join(
            f"""<item><title>Invalid IMDb {index}</title>
              <enclosure url='https://site/invalid-imdb-{index}.torrent'/>
              <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed'
                name='imdbid' value='{value}'/>
            </item>"""
            for index, value in enumerate(invalid_values)
        )

        with loaded_module() as module:
            _RequestUtils.response = _Response()
            _RequestUtils.response.text = f"<?xml version='1.0'?><rss><channel>{items}</channel></rss>"
            plugin = object.__new__(module.JackettExtend)
            plugin._timeout = 12
            plugin._proxy = False
            plugin._last_error = None
            plugin._state_lock = module.JackettExtend._state_lock

            result = plugin._JackettExtend__parse_torznab_xml(
                "https://jackett.invalid/results",
                site={"name": "Nyaa"},
            )

            self.assertEqual(len(result), len(invalid_values))
            for torrent in result:
                self.assertIsNone(torrent.media_source)
                self.assertIsNone(torrent.media_id)

    def test_parser_falls_back_for_negative_size_and_counts(self):
        with loaded_module() as module:
            _RequestUtils.response = _Response()
            _RequestUtils.response.text = """<?xml version='1.0'?><rss><channel>
              <item><title>Negative numerics</title><enclosure url='https://site/negative.torrent'/>
                <size>-1</size>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='seeders' value='-2'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='peers' value='-3'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='grabs' value='-4'/>
              </item>
            </channel></rss>"""
            plugin = object.__new__(module.JackettExtend)
            plugin._timeout = 12
            plugin._proxy = False
            plugin._last_error = None
            plugin._state_lock = module.JackettExtend._state_lock

            result = plugin._JackettExtend__parse_torznab_xml(
                "https://jackett.invalid/results",
                site={"name": "Nyaa"},
            )

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].size, 0.0)
            self.assertEqual(result[0].seeders, 0)
            self.assertEqual(result[0].peers, 0)
            self.assertEqual(result[0].grabs, 0)

    def test_parser_falls_back_for_invalid_size_and_counts_and_is_strict_json_safe(self):
        for index, value in enumerate(("not-a-number", "NaN", "Infinity", "-Infinity")):
            with self.subTest(value=value), loaded_module() as module:
                _RequestUtils.response = _Response()
                _RequestUtils.response.text = f"""<?xml version='1.0'?><rss><channel>
                  <item><title>Nonfinite numerics {index}</title>
                    <enclosure url='https://site/nonfinite-{index}.torrent'/>
                    <size>{value}</size>
                    <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='seeders' value='{value}'/>
                    <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='peers' value='{value}'/>
                    <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='grabs' value='{value}'/>
                  </item>
                </channel></rss>"""
                plugin = object.__new__(module.JackettExtend)
                plugin._timeout = 12
                plugin._proxy = False
                plugin._last_error = None
                plugin._state_lock = module.JackettExtend._state_lock

                result = plugin._JackettExtend__parse_torznab_xml(
                    "https://jackett.invalid/results",
                    site={"name": "Nyaa"},
                )

                self.assertEqual(len(result), 1)
                self.assertEqual(result[0].size, 0.0)
                self.assertEqual(result[0].seeders, 0)
                self.assertEqual(result[0].peers, 0)
                self.assertEqual(result[0].grabs, 0)
                json.dumps(result[0].__dict__, allow_nan=False)

    def test_parser_rejects_invalid_promotion_factors_without_marking_free(self):
        for index, value in enumerate(("NaN", "Infinity", "-Infinity", "-1")):
            with self.subTest(value=value), loaded_module() as module:
                _RequestUtils.response = _Response()
                _RequestUtils.response.text = f"""<?xml version='1.0'?><rss><channel>
                  <item><title>Invalid factors {index}</title>
                    <enclosure url='https://site/invalid-factors-{index}.torrent'/>
                    <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed'
                      name='uploadvolumefactor' value='{value}'/>
                    <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed'
                      name='downloadvolumefactor' value='{value}'/>
                  </item>
                </channel></rss>"""
                plugin = object.__new__(module.JackettExtend)
                plugin._timeout = 12
                plugin._proxy = False
                plugin._last_error = None
                plugin._state_lock = module.JackettExtend._state_lock

                result = plugin._JackettExtend__parse_torznab_xml(
                    "https://jackett.invalid/results",
                    site={"name": "Nyaa"},
                )

                self.assertEqual(len(result), 1)
                self.assertIsNone(result[0].uploadvolumefactor)
                self.assertIsNone(result[0].downloadvolumefactor)
                json.dumps(result[0].__dict__, allow_nan=False)

    def test_parser_duplicate_infohash_prefers_http_torrent_over_magnet(self):
        with loaded_module() as module:
            _RequestUtils.response = _Response()
            _RequestUtils.response.text = """<?xml version='1.0'?><rss><channel>
              <item><title>Magnet first</title><guid>g1</guid><enclosure url='magnet:?xt=urn:btih:same'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='infohash' value='same'/>
              </item>
              <item><title>HTTP second</title><guid>g2</guid><enclosure url='https://site/dl/same.torrent'/>
                <torznab:attr xmlns:torznab='http://torznab.com/schemas/2015/feed' name='infohash' value='same'/>
              </item>
            </channel></rss>"""
            plugin = object.__new__(module.JackettExtend)
            plugin._timeout = 12
            plugin._proxy = False
            plugin._last_error = None
            plugin._state_lock = module.JackettExtend._state_lock
            result = plugin._JackettExtend__parse_torznab_xml(
                "https://jackett.invalid/results?q=private-title&apikey=secret",
                site={"name": "Nyaa"},
            )
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].title, "HTTP second")
            self.assertTrue(result[0].enclosure.startswith("https://"))

    def test_parser_rejects_http_200_torznab_error_without_leaking_details(self):
        with loaded_module() as module:
            _RequestUtils.response = _Response()
            _RequestUtils.response.text = (
                '<?xml version="1.0"?><error code="100" '
                'description="secret diagnostic"/>'
            )
            logs = []
            module.logger = types.SimpleNamespace(
                warning=lambda message: logs.append(message),
            )
            plugin = object.__new__(module.JackettExtend)
            plugin._timeout = 12
            plugin._proxy = False
            plugin._last_error = None
            plugin._state_lock = module.JackettExtend._state_lock
            result = plugin._JackettExtend__parse_torznab_xml(
                "https://jackett.invalid/results?q=private-title&apikey=secret",
            )
            self.assertEqual(result, [])
            self.assertEqual(plugin._last_search_error, "torznab_error")
            rendered_logs = " ".join(logs)
            self.assertNotIn("description", rendered_logs)
            self.assertNotIn("secret diagnostic", rendered_logs)
            self.assertNotIn("secret", rendered_logs)
            self.assertNotIn("private-title", rendered_logs)

    def test_parser_rejects_doctype_and_oversized_body(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._timeout = 12
            plugin._proxy = False
            plugin._last_error = None
            plugin._state_lock = module.JackettExtend._state_lock
            for body in (
                "<!DOCTYPE rss [<!ENTITY x 'expanded'>]><rss><channel></channel></rss>",
                "<rss><channel>" + "x" * (module.JackettExtend.TORZNAB_MAX_XML_BYTES + 1) + "</channel></rss>",
            ):
                _RequestUtils.response = _Response()
                _RequestUtils.response.text = body
                self.assertEqual(
                    plugin._JackettExtend__parse_torznab_xml("https://jackett.invalid/results?q=x"),
                    [],
                )

            plugin.TORZNAB_MAX_ITEMS = 1
            _RequestUtils.response = _Response()
            _RequestUtils.response.text = (
                "<rss><channel>"
                "<item><title>One</title><enclosure url='https://site/one.torrent'/></item>"
                "<item><title>Two</title><enclosure url='https://site/two.torrent'/></item>"
                "</channel></rss>"
            )
            self.assertEqual(
                plugin._JackettExtend__parse_torznab_xml("https://jackett.invalid/results?q=x"),
                [],
            )

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

    def test_sync_stage_failure_keeps_last_sync_ok_false(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._sync_stop_event = __import__("threading").Event()
            plugin._sync_generation = 9
            plugin._fetch_ok = True
            plugin._sync_ready = True
            plugin._authoritative_indexers = [
                {"indexer_id": "nyaa", "domain": "jackett_extend.nyaa"},
            ]
            plugin._indexers = list(plugin._authoritative_indexers)
            plugin._indexer_sites = []

            class BrokenSites:
                @staticmethod
                def add_indexer(*_args, **_kwargs):
                    raise RuntimeError("broken helper")

            plugin.sites_helper = BrokenSites()
            plugin.get_status = lambda generation=None, config_snapshot=None: True
            plugin._JackettExtend__register_site = lambda *_args, **_kwargs: False
            plugin._JackettExtend__sync_remove_stale_sites = lambda *_args, **_kwargs: True
            plugin._JackettExtend__sync_all(generation=9)
            self.assertFalse(plugin._last_sync_ok)

    def test_old_generation_is_rejected_after_reload_event_replacement(self):
        with loaded_module() as module:
            plugin = object.__new__(module.JackettExtend)
            plugin._sync_stop_event = __import__("threading").Event()
            plugin._sync_generation = 4

            self.assertFalse(plugin._sync_is_current(generation=3))
            self.assertTrue(plugin._sync_is_current(generation=4))

    def test_all_stale_selection_stays_empty_and_does_not_cleanup_sites(self):
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

            def status(generation=None, config_snapshot=None):
                return True

            def cleanup_selection(_snapshot, generation=None):
                # Keep the finite stale whitelist; cleanup must not turn it
                # into the all-indexers meaning.
                return True

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

            self.assertEqual(registered, [])
            self.assertEqual(cleanup_snapshots, [])


class JackettSyncBoundaryTest(unittest.TestCase):
    @staticmethod
    def _active_plugin(module, generation=1):
        plugin = object.__new__(module.JackettExtend)
        plugin._sync_stop_event = threading.Event()
        plugin._sync_generation = generation
        return plugin

    @staticmethod
    def _event_manager(fail=False, invalidate=None):
        state = types.SimpleNamespace(calls=[])

        class EventManager:
            def send_event(self, event, payload):
                state.calls.append((event, payload))
                if invalidate is not None:
                    invalidate()
                if fail:
                    raise RuntimeError("event unavailable")

        return EventManager(), state

    def test_register_site_adds_rows_and_supports_both_oper_import_paths(self):
        for fallback in (False, True):
            with self.subTest(fallback=fallback), loaded_module() as module:
                state = types.SimpleNamespace(adds=[], updates=[], lookups=[])

                class FakeSiteOper:
                    def get_by_domain(self, domain):
                        state.lookups.append(domain)
                        return None

                    def add(self, **payload):
                        state.adds.append(payload)

                    def update(self, site_id, payload):
                        state.updates.append((site_id, payload))

                eventmanager, events = self._event_manager()
                event_type = types.SimpleNamespace(SiteUpdated="SiteUpdated")
                plugin = self._active_plugin(module)
                indexer = {
                    "name": "Nyaa",
                    "domain": "jackett_extend.nyaa",
                    "public": True,
                    "proxy": True,
                }

                with site_oper_modules(
                    FakeSiteOper,
                    eventmanager=eventmanager,
                    event_type=event_type,
                    fallback=fallback,
                ):
                    result = plugin._JackettExtend__register_site(indexer, generation=1)

                self.assertTrue(result)
                self.assertEqual(state.lookups, ["jackett_extend.nyaa"])
                self.assertEqual(state.adds, [{
                    "name": "Nyaa",
                    "domain": "jackett_extend.nyaa",
                    "url": "https://jackett_extend.nyaa/",
                    "public": 1,
                    "proxy": 1,
                    "is_active": True,
                    "pri": 1,
                }])
                self.assertEqual(state.updates, [])
                self.assertEqual(
                    events.calls,
                    [("SiteUpdated", {"domain": "jackett_extend.nyaa"})],
                )

    def test_register_site_updates_only_plugin_owned_fields(self):
        with loaded_module() as module:
            existing = types.SimpleNamespace(
                id=41,
                domain="jackett_extend.nyaa",
                is_active=False,
                pri=99,
                proxy=1,
                custom_flag="keep",
            )
            state = types.SimpleNamespace(updates=[])

            class FakeSiteOper:
                def get_by_domain(self, domain):
                    self.lookup = domain
                    return existing

                def update(self, site_id, payload):
                    state.updates.append((site_id, payload))

                def add(self, **_payload):
                    raise AssertionError("existing rows must not use add")

            eventmanager, events = self._event_manager()
            event_type = types.SimpleNamespace(SiteUpdated="SiteUpdated")
            plugin = self._active_plugin(module)
            indexer = {
                "name": "Nyaa renamed",
                "domain": "jackett_extend.nyaa",
                "public": False,
                "proxy": False,
            }

            with site_oper_modules(
                FakeSiteOper,
                eventmanager=eventmanager,
                event_type=event_type,
                fallback=True,
            ):
                result = plugin._JackettExtend__register_site(indexer, generation=1)

            self.assertTrue(result)
            self.assertEqual(
                state.updates,
                [(41, {
                    "name": "Nyaa renamed",
                    "url": "https://jackett_extend.nyaa/",
                    "public": 0,
                })],
            )
            self.assertEqual(existing.is_active, False)
            self.assertEqual(existing.pri, 99)
            self.assertEqual(existing.proxy, 1)
            self.assertEqual(existing.custom_flag, "keep")
            self.assertEqual(
                events.calls,
                [("SiteUpdated", {"domain": "jackett_extend.nyaa"})],
            )

    def test_register_site_add_conflict_rechecks_and_updates(self):
        with loaded_module() as module:
            existing = types.SimpleNamespace(id=7, domain="jackett_extend.nyaa")
            state = types.SimpleNamespace(adds=[], lookups=[], updates=[])

            class FakeSiteOper:
                def get_by_domain(self, domain):
                    state.lookups.append(domain)
                    return None if len(state.lookups) == 1 else existing

                def add(self, **payload):
                    state.adds.append(payload)
                    raise RuntimeError("duplicate")

                def update(self, site_id, payload):
                    state.updates.append((site_id, payload))

            eventmanager, events = self._event_manager()
            event_type = types.SimpleNamespace(SiteUpdated="SiteUpdated")
            plugin = self._active_plugin(module)

            with site_oper_modules(
                FakeSiteOper,
                eventmanager=eventmanager,
                event_type=event_type,
            ):
                result = plugin._JackettExtend__register_site({
                    "name": "Nyaa",
                    "domain": "jackett_extend.nyaa",
                    "public": True,
                    "proxy": False,
                }, generation=1)

            self.assertTrue(result)
            self.assertEqual(len(state.adds), 1)
            self.assertEqual(
                state.updates,
                [(7, {
                    "name": "Nyaa",
                    "url": "https://jackett_extend.nyaa/",
                    "public": 1,
                })],
            )
            self.assertEqual(state.lookups, ["jackett_extend.nyaa", "jackett_extend.nyaa"])
            self.assertEqual(
                events.calls,
                [("SiteUpdated", {"domain": "jackett_extend.nyaa"})],
            )

    def test_register_site_event_failure_reports_false_after_db_success(self):
        with loaded_module() as module:
            state = types.SimpleNamespace(adds=[])

            class FakeSiteOper:
                def get_by_domain(self, _domain):
                    return None

                def add(self, **payload):
                    state.adds.append(payload)

            eventmanager, events = self._event_manager(fail=True)
            event_type = types.SimpleNamespace(SiteUpdated="SiteUpdated")
            plugin = self._active_plugin(module)

            with site_oper_modules(
                FakeSiteOper,
                eventmanager=eventmanager,
                event_type=event_type,
            ):
                result = plugin._JackettExtend__register_site({
                    "name": "Nyaa",
                    "domain": "jackett_extend.nyaa",
                    "public": True,
                }, generation=1)

            self.assertFalse(result)
            self.assertEqual(len(state.adds), 1)
            self.assertEqual(
                events.calls,
                [("SiteUpdated", {"domain": "jackett_extend.nyaa"})],
            )

    def test_remove_stale_sites_deletes_only_virtual_rows_and_emits_deleted(self):
        with loaded_module() as module:
            records = [
                types.SimpleNamespace(id=1, domain="jackett_extend.old"),
                types.SimpleNamespace(id=2, domain="ordinary.example"),
                types.SimpleNamespace(id=3, domain="jackett_extend.keep"),
            ]
            state = types.SimpleNamespace(deleted=[])

            class FakeSiteOper:
                def list(self):
                    return records

                def delete(self, site_id):
                    state.deleted.append(site_id)

            eventmanager, events = self._event_manager()
            event_type = types.SimpleNamespace(SiteDeleted="SiteDeleted")
            plugin = self._active_plugin(module)
            plugin._sync_ready = True

            with site_oper_modules(
                FakeSiteOper,
                eventmanager=eventmanager,
                event_type=event_type,
            ):
                result = plugin._JackettExtend__sync_remove_stale_sites(
                    [{"domain": "jackett_extend.keep"}],
                    generation=1,
                )

            self.assertTrue(result)
            self.assertEqual(state.deleted, [1])
            self.assertEqual(
                events.calls,
                [("SiteDeleted", {"site_id": 1})],
            )

    def test_remove_stale_sites_event_failure_makes_stage_fail(self):
        with loaded_module() as module:
            state = types.SimpleNamespace(deleted=[])

            class FakeSiteOper:
                def list(self):
                    return [types.SimpleNamespace(id=1, domain="jackett_extend.old")]

                def delete(self, site_id):
                    state.deleted.append(site_id)

            eventmanager, events = self._event_manager(fail=True)
            event_type = types.SimpleNamespace(SiteDeleted="SiteDeleted")
            plugin = self._active_plugin(module)
            plugin._sync_ready = True

            with site_oper_modules(
                FakeSiteOper,
                eventmanager=eventmanager,
                event_type=event_type,
            ):
                result = plugin._JackettExtend__sync_remove_stale_sites(
                    [{"domain": "jackett_extend.keep"}],
                    generation=1,
                )

            self.assertFalse(result)
            self.assertEqual(state.deleted, [1])
            self.assertEqual(
                events.calls,
                [("SiteDeleted", {"site_id": 1})],
            )

    def test_remove_stale_sites_stops_after_generation_invalidates(self):
        with loaded_module() as module:
            state = types.SimpleNamespace(deleted=[])
            plugin = self._active_plugin(module)

            class FakeSiteOper:
                def list(self):
                    return [
                        types.SimpleNamespace(id=1, domain="jackett_extend.first"),
                        types.SimpleNamespace(id=2, domain="jackett_extend.second"),
                    ]

                def delete(self, site_id):
                    state.deleted.append(site_id)
                    with plugin._state_lock:
                        plugin._sync_generation += 1

            eventmanager, events = self._event_manager()
            event_type = types.SimpleNamespace(SiteDeleted="SiteDeleted")
            plugin._sync_ready = True

            with site_oper_modules(
                FakeSiteOper,
                eventmanager=eventmanager,
                event_type=event_type,
            ):
                result = plugin._JackettExtend__sync_remove_stale_sites(
                    [{"domain": "jackett_extend.keep"}],
                    generation=1,
                )

            self.assertFalse(result)
            self.assertEqual(state.deleted, [1])
            self.assertEqual(
                events.calls,
                [("SiteDeleted", {"site_id": 1})],
            )

    def test_reload_uses_real_stop_and_replaces_generation_event(self):
        with loaded_module() as module:
            class FakeCronTrigger:
                @classmethod
                def from_crontab(cls, expression, timezone=None):
                    return cls()

            created = []

            class FakeThread:
                def __init__(self, target, kwargs, name, daemon):
                    self.target = target
                    self.kwargs = kwargs
                    self.name = name
                    self.daemon = daemon
                    self.started = False
                    created.append(self)

                def start(self):
                    self.started = True

                def is_alive(self):
                    return False

                def join(self, timeout=None):
                    return None

            module.CronTrigger = FakeCronTrigger
            module.threading = types.SimpleNamespace(
                Event=threading.Event,
                Thread=FakeThread,
                current_thread=threading.current_thread,
            )
            plugin = object.__new__(module.JackettExtend)

            plugin.init_plugin({
                "enabled": True,
                "host": "https://first.invalid",
                "api_key": "first-key",
            })
            first_event = plugin._sync_stop_event
            first_generation = plugin._sync_generation
            self.assertEqual(len(created), 1)
            self.assertTrue(created[0].started)
            self.assertEqual(created[0].kwargs, {"generation": first_generation})

            plugin.init_plugin({
                "enabled": True,
                "host": "https://second.invalid",
                "api_key": "second-key",
            })
            second_event = plugin._sync_stop_event
            second_generation = plugin._sync_generation

            self.assertTrue(first_event.is_set())
            self.assertIsNot(first_event, second_event)
            self.assertGreaterEqual(second_generation, first_generation + 2)
            self.assertEqual(len(created), 2)
            self.assertTrue(created[1].started)
            self.assertEqual(created[1].kwargs, {"generation": second_generation})

            plugin.stop_service()
            self.assertTrue(second_event.is_set())


if __name__ == "__main__":
    unittest.main()
