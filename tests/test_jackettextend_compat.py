import asyncio
import inspect
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import MappingProxyType


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PATH = ROOT / "plugins.v3" / "jackettextend"


def load_helper(filename, name):
    spec = importlib.util.spec_from_file_location(name, PACKAGE_PATH / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INDEXERS = load_helper("_indexers.py", "jackettextend_indexers_isolated")
COMPAT = load_helper("_host_compat.py", "jackettextend_host_compat_isolated")


class Owner:
    plugin_name = "JackettExtend"

    def __init__(self, name="plugin"):
        self.name = name
        self.sync_calls = []
        self.async_calls = []
        self.page_calls = []

    def search_torrents(self, *args, **kwargs):
        self.sync_calls.append((args, kwargs))
        return [f"{self.name}-sync"]

    async def async_search_torrents(self, *args, **kwargs):
        self.async_calls.append((args, kwargs))
        return [f"{self.name}-async"]

    def get_search_page_size(self, *args, **kwargs):
        self.page_calls.append((args, kwargs))
        return None

    @staticmethod
    def _is_virtual_site(site, domain=""):
        return INDEXERS.is_virtual_site(site, domain)


class ExplodingOwner(Owner):
    def search_torrents(self, *args, **kwargs):
        self.sync_calls.append((args, kwargs))
        raise RuntimeError("sync owner failure")

    async def async_search_torrents(self, *args, **kwargs):
        self.async_calls.append((args, kwargs))
        raise RuntimeError("async owner failure")


def make_chain():
    calls = []

    class ChainBase:
        def search_site_torrents(self, site, keyword, mtype=None, page=0):
            calls.append(("sync", site, keyword, page))
            return ["host-sync"]

        async def async_search_site_torrents(self, site, keyword, mtype=None, page=0):
            calls.append(("async", site, keyword, page))
            return ["host-async"]

        def get_search_page_size(self, site, keyword=None):
            calls.append(("page", site, keyword))
            return 50

    return ChainBase, calls


class HostCompatTest(unittest.TestCase):
    def setUp(self):
        self.previous_app = sys.modules.get("app")
        self.previous_chain = sys.modules.get("app.chain")
        self.ChainBase, self.host_calls = make_chain()
        app = types.ModuleType("app")
        chain = types.ModuleType("app.chain")
        chain.ChainBase = self.ChainBase
        app.chain = chain
        sys.modules["app"] = app
        sys.modules["app.chain"] = chain

    def tearDown(self):
        # Restore an owner if a test intentionally left one installed.
        state = getattr(self.ChainBase, COMPAT._STATE_ATTR, None)
        if isinstance(state, dict):
            owner = COMPAT._owner_from_state(state)
            if owner is not None:
                COMPAT.uninstall(owner)
        if self.previous_app is None:
            sys.modules.pop("app", None)
        else:
            sys.modules["app"] = self.previous_app
        if self.previous_chain is None:
            sys.modules.pop("app.chain", None)
        else:
            sys.modules["app.chain"] = self.previous_chain

    def test_predicate_routes_only_jackett_sites_and_leaves_page_size_to_host(self):
        owner = Owner()
        originals = {
            name: inspect.getattr_static(self.ChainBase, name)
            for name in ("search_site_torrents", "async_search_site_torrents")
        }
        page_original = inspect.getattr_static(self.ChainBase, "get_search_page_size")

        self.assertEqual(
            COMPAT._METHODS,
            ("search_site_torrents", "async_search_site_torrents"),
        )
        self.assertTrue(COMPAT.install(owner))
        chain = self.ChainBase()

        marked = {"domain": "jackett_extend.nyaa"}
        marked_proxy = MappingProxyType({"domain": "jackett_extend.proxy"})
        ordinary = {"domain": "ordinary.example", "owned": True}
        self.assertEqual(chain.search_site_torrents(marked, "title"), ["plugin-sync"])
        self.assertEqual(chain.search_site_torrents(marked_proxy, "title"), ["plugin-sync"])
        self.assertEqual(chain.search_site_torrents(ordinary, "title"), ["host-sync"])
        self.assertEqual(chain.search_site_torrents({}, "global"), ["host-sync"])
        self.assertEqual(chain.search_site_torrents([], "invalid-site"), ["host-sync"])
        self.assertEqual(asyncio.run(chain.async_search_site_torrents(marked, "title")), ["plugin-async"])
        self.assertEqual(asyncio.run(chain.async_search_site_torrents(ordinary, "title")), ["host-async"])
        self.assertEqual(asyncio.run(chain.async_search_site_torrents({}, "global")), ["host-async"])
        self.assertEqual(COMPAT.status()["methods"], COMPAT._METHODS)
        self.assertNotIn("get_search_page_size", COMPAT.status()["methods"])
        self.assertIs(inspect.getattr_static(self.ChainBase, "get_search_page_size"), page_original)
        self.assertEqual(chain.get_search_page_size(marked, "title"), 50)
        self.assertEqual(chain.get_search_page_size(ordinary, "title"), 50)
        self.assertEqual(chain.get_search_page_size({}, "global"), 50)
        self.assertEqual(len(owner.sync_calls), 2)
        self.assertEqual(len(owner.async_calls), 1)
        self.assertEqual(len(owner.page_calls), 0)
        self.assertTrue(COMPAT.uninstall(owner))
        for name, original in originals.items():
            self.assertIs(getattr(self.ChainBase, name), original)
        self.assertIs(inspect.getattr_static(self.ChainBase, "get_search_page_size"), page_original)

    def test_install_is_idempotent_reload_safe_and_old_owner_cannot_uninstall(self):
        first = Owner()
        self.assertTrue(COMPAT.install(first))
        wrapped = self.ChainBase.search_site_torrents
        self.assertTrue(COMPAT.install(first))
        self.assertIs(self.ChainBase.search_site_torrents, wrapped)
        second = Owner()
        self.assertTrue(COMPAT.install(second))
        self.assertIs(self.ChainBase.search_site_torrents, wrapped)
        self.assertEqual(
            self.ChainBase().search_site_torrents(
                {"domain": "jackett_extend.a", "owned": True}, "x"
            ),
            ["plugin-sync"],
        )
        self.assertEqual(first.sync_calls, [])
        self.assertEqual(len(second.sync_calls), 1)
        self.assertFalse(COMPAT.uninstall(first))
        self.assertTrue(COMPAT.status()["installed"])
        self.assertTrue(COMPAT.uninstall(second))
        self.assertFalse(COMPAT.status()["installed"])

    def test_host_capability_attributes_do_not_change_current_boundary(self):
        original = self.ChainBase.search_site_torrents
        self.ChainBase.supports_targeted_plugin_route = True
        owner = Owner()
        self.assertFalse(hasattr(COMPAT, "host_supports_targeted_route"))
        self.assertTrue(COMPAT.install(owner))
        self.assertIsNot(self.ChainBase.search_site_torrents, original)
        self.assertEqual(
            self.ChainBase().search_site_torrents({"domain": "jackett_extend.x"}, "x"),
            ["plugin-sync"],
        )

    def test_custom_predicate_can_claim_a_non_legacy_site(self):
        owner = Owner()
        self.assertTrue(COMPAT.install(owner, predicate=lambda site, _domain: site.get("owned") is True))
        result = self.ChainBase().search_site_torrents({"domain": "custom.example", "owned": True}, "x")
        self.assertEqual(result, ["plugin-sync"])

    def test_predicate_errors_fall_back_to_host_for_both_boundaries(self):
        owner = Owner()

        def broken_predicate(site, domain):
            raise RuntimeError(f"predicate failure: {domain}")

        self.assertTrue(COMPAT.install(owner, predicate=broken_predicate))
        chain = self.ChainBase()
        site = {"domain": "jackett_extend.broken"}
        self.assertEqual(chain.search_site_torrents(site, "title"), ["host-sync"])
        self.assertEqual(
            asyncio.run(chain.async_search_site_torrents(site, "title")),
            ["host-async"],
        )
        self.assertEqual(owner.sync_calls, [])
        self.assertEqual(owner.async_calls, [])

    def test_owner_search_errors_are_isolated_and_fall_back_to_host(self):
        owner = ExplodingOwner()
        self.assertTrue(COMPAT.install(owner))
        chain = self.ChainBase()
        site = {"domain": "jackett_extend.owner-error"}

        self.assertEqual(chain.search_site_torrents(site, "title"), ["host-sync"])
        self.assertEqual(
            asyncio.run(chain.async_search_site_torrents(site, "title")),
            ["host-async"],
        )
        self.assertEqual(len(owner.sync_calls), 1)
        self.assertEqual(len(owner.async_calls), 1)

    def test_generation_race_never_calls_the_stale_owner(self):
        first = Owner("first")
        second = Owner("second")
        predicate_calls = []

        def switch_owner(site, domain):
            predicate_calls.append((site, domain))
            if len(predicate_calls) == 1:
                self.assertTrue(COMPAT.install(second))
            return True

        self.assertTrue(COMPAT.install(first, predicate=switch_owner))
        result = self.ChainBase().search_site_torrents(
            {"domain": "jackett_extend.race"}, "title"
        )

        # The predicate can race with reload, but an in-flight call must not
        # dispatch into the owner generation that was replaced mid-decision.
        self.assertEqual(first.sync_calls, [])
        self.assertIn(result, (["second-sync"], ["host-sync"]))
        self.assertLessEqual(len(second.sync_calls), 1)

    def test_reload_migrates_legacy_three_method_state_before_new_install(self):
        originals = {
            name: inspect.getattr_static(self.ChainBase, name)
            for name in (
                "search_site_torrents",
                "async_search_site_torrents",
                "get_search_page_size",
            )
        }

        def legacy_sync(*args, **kwargs):
            return ["legacy-sync"]

        async def legacy_async(*args, **kwargs):
            return ["legacy-async"]

        def legacy_page(*args, **kwargs):
            return None

        legacy_wrappers = {
            "search_site_torrents": legacy_sync,
            "async_search_site_torrents": legacy_async,
            "get_search_page_size": legacy_page,
        }
        legacy_state = {
            "version": 1,
            "owner_ref": None,
            "owner": None,
            "predicate_ref": None,
            "predicate": None,
            "originals": originals,
            "defined_here": {name: True for name in originals},
            "wrappers": legacy_wrappers,
        }
        for name, wrapper in legacy_wrappers.items():
            setattr(self.ChainBase, name, wrapper)
        setattr(self.ChainBase, COMPAT._STATE_ATTR, legacy_state)

        owner = Owner()
        self.assertTrue(COMPAT.install(owner))
        self.assertIs(
            inspect.getattr_static(self.ChainBase, "get_search_page_size"),
            originals["get_search_page_size"],
        )
        self.assertIsNot(self.ChainBase.search_site_torrents, legacy_sync)
        self.assertEqual(
            self.ChainBase().search_site_torrents(
                {"domain": "jackett_extend.reload"}, "title"
            ),
            ["plugin-sync"],
        )
        self.assertEqual(COMPAT.status()["methods"], COMPAT._METHODS)

    def test_module_reload_uses_new_bridge_token_and_wrapper_closures(self):
        first = Owner("first")
        self.assertTrue(COMPAT.install(first))
        old_sync = self.ChainBase.search_site_torrents
        old_async = self.ChainBase.async_search_site_torrents
        page_original = inspect.getattr_static(self.ChainBase, "get_search_page_size")

        reloaded = load_helper(
            "_host_compat.py", "jackettextend_host_compat_reloaded"
        )
        second = Owner("second")
        self.assertTrue(reloaded.install(second))
        self.assertIsNot(self.ChainBase.search_site_torrents, old_sync)
        self.assertIsNot(self.ChainBase.async_search_site_torrents, old_async)
        self.assertIs(
            inspect.getattr_static(self.ChainBase, "get_search_page_size"),
            page_original,
        )
        self.assertFalse(COMPAT.uninstall(first))
        self.assertEqual(
            self.ChainBase().search_site_torrents(
                {"domain": "jackett_extend.reloaded"}, "title"
            ),
            ["second-sync"],
        )
        self.assertTrue(reloaded.uninstall(second))

    def test_uninstall_does_not_overwrite_third_party_method_replacement(self):
        owner = Owner()
        original_sync = inspect.getattr_static(self.ChainBase, "search_site_torrents")
        original_async = inspect.getattr_static(self.ChainBase, "async_search_site_torrents")
        original_page = inspect.getattr_static(self.ChainBase, "get_search_page_size")
        self.assertTrue(COMPAT.install(owner))

        def third_party_sync(*args, **kwargs):
            return ["third-party"]

        setattr(self.ChainBase, "search_site_torrents", third_party_sync)
        self.assertTrue(COMPAT.uninstall(owner))
        self.assertIs(
            inspect.getattr_static(self.ChainBase, "search_site_torrents"),
            third_party_sync,
        )
        self.assertIs(inspect.getattr_static(self.ChainBase, "async_search_site_torrents"), original_async)
        self.assertIs(inspect.getattr_static(self.ChainBase, "get_search_page_size"), original_page)
        self.assertIsNot(original_sync, third_party_sync)


class IndexerHelpersTest(unittest.TestCase):
    def test_selection_and_profiles_are_pure(self):
        self.assertEqual(
            INDEXERS.parse_indexer_sites("['Nyaa', ' AnimeTosho ']"),
            ["nyaa", "animetosho"],
        )
        raw = [
            {"id": "foo/bar", "name": "Foo", "privacy": "public", "caps": [
                {"ID": "3000", "Name": "Music"},
                {"ID": "5000", "Name": "TV"},
            ]},
            None,
        ]
        profiles = INDEXERS.build_indexer_profiles(raw, "https://jackett.invalid/", True)
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["indexer_id"], "foo/bar")
        self.assertEqual(profiles[0]["privacy"], "public")
        self.assertTrue(profiles[0]["public"])
        self.assertIn("foo%2Fbar", profiles[0]["domain"])
        self.assertEqual(profiles[0]["category"]["music"][0]["id"], "3000")
        self.assertEqual(
            INDEXERS.apply_indexer_selection(profiles, ["missing"], explicit=True),
            [],
        )

    def test_jackett_type_maps_to_privacy_without_guessing_unknown_values(self):
        raw = [
            {"id": "pub", "name": "Public", "type": "public"},
            {"id": "semi", "name": "Semi", "type": "semi-private"},
            {"id": "priv", "name": "Private", "type": "private"},
            {"id": "legacy", "name": "Legacy", "privacy": "private"},
            {"id": "odd", "name": "Odd", "type": "not-a-jackett-type"},
            {"id": "explicit-unknown", "name": "Explicit unknown", "type": "unknown"},
            {"id": "missing", "name": "Missing", "public": True},
        ]

        profiles = INDEXERS.build_indexer_profiles(raw, "https://jackett.invalid", False)
        by_id = {profile["indexer_id"]: profile for profile in profiles}

        self.assertEqual(by_id["pub"]["privacy"], "public")
        self.assertTrue(by_id["pub"]["public"])
        self.assertEqual(by_id["semi"]["privacy"], "semi-private")
        self.assertFalse(by_id["semi"]["public"])
        self.assertEqual(by_id["priv"]["privacy"], "private")
        self.assertFalse(by_id["priv"]["public"])
        self.assertEqual(by_id["legacy"]["privacy"], "private")
        self.assertIsNone(by_id["odd"]["privacy"])
        self.assertFalse(by_id["odd"]["public"])
        self.assertEqual(by_id["explicit-unknown"]["privacy"], "unknown")
        self.assertFalse(by_id["explicit-unknown"]["public"])
        self.assertIsNone(by_id["missing"]["privacy"])
        self.assertTrue(by_id["missing"]["public"])


if __name__ == "__main__":
    unittest.main()
