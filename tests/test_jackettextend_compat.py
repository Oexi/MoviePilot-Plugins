import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


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

    def __init__(self):
        self.sync_calls = []
        self.async_calls = []
        self.page_calls = []

    def search_torrents(self, *args, **kwargs):
        self.sync_calls.append((args, kwargs))
        return ["plugin-sync"]

    async def async_search_torrents(self, *args, **kwargs):
        self.async_calls.append((args, kwargs))
        return ["plugin-async"]

    def get_search_page_size(self, *args, **kwargs):
        self.page_calls.append((args, kwargs))
        return None


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

    def test_marked_and_legacy_virtual_sites_route_all_boundaries(self):
        owner = Owner()
        originals = {
            name: getattr(self.ChainBase, name)
            for name in COMPAT._METHODS
        }
        self.assertTrue(COMPAT.install(owner))
        chain = self.ChainBase()

        marked = {"domain": "ordinary.example", "plugin": "JackettExtend"}
        legacy = {"domain": "jackett_extend.nyaa"}
        ordinary = {"domain": "ordinary.example", "plugin": "OtherPlugin"}
        self.assertEqual(chain.search_site_torrents(marked, "title"), ["plugin-sync"])
        self.assertEqual(chain.search_site_torrents(legacy, "title"), ["plugin-sync"])
        self.assertEqual(chain.search_site_torrents(ordinary, "title"), ["host-sync"])
        self.assertEqual(chain.search_site_torrents({}, "global"), ["host-sync"])
        self.assertEqual(asyncio.run(chain.async_search_site_torrents(marked, "title")), ["plugin-async"])
        self.assertEqual(asyncio.run(chain.async_search_site_torrents(ordinary, "title")), ["host-async"])
        self.assertIsNone(chain.get_search_page_size(marked, "title"))
        self.assertEqual(chain.get_search_page_size(ordinary, "title"), 50)
        self.assertEqual(chain.get_search_page_size({}, "global"), 50)
        self.assertEqual(len(owner.sync_calls), 2)
        self.assertEqual(len(owner.async_calls), 1)
        self.assertEqual(len(owner.page_calls), 1)
        self.assertTrue(COMPAT.uninstall(owner))
        for name, original in originals.items():
            self.assertIs(getattr(self.ChainBase, name), original)

    def test_install_is_idempotent_reload_safe_and_old_owner_cannot_uninstall(self):
        first = Owner()
        self.assertTrue(COMPAT.install(first))
        wrapped = self.ChainBase.search_site_torrents
        self.assertTrue(COMPAT.install(first))
        self.assertIs(self.ChainBase.search_site_torrents, wrapped)
        second = Owner()
        self.assertTrue(COMPAT.install(second))
        self.assertIs(self.ChainBase.search_site_torrents, wrapped)
        self.assertEqual(self.ChainBase().search_site_torrents({"domain": "jackett_extend.a"}, "x"), ["plugin-sync"])
        self.assertEqual(first.sync_calls, [])
        self.assertEqual(len(second.sync_calls), 1)
        self.assertFalse(COMPAT.uninstall(first))
        self.assertTrue(COMPAT.status()["installed"])
        self.assertTrue(COMPAT.uninstall(second))
        self.assertFalse(COMPAT.status()["installed"])

    def test_official_targeted_route_skips_patch(self):
        original = self.ChainBase.search_site_torrents
        self.ChainBase.supports_targeted_plugin_route = True
        self.assertTrue(COMPAT.host_supports_targeted_route(self.ChainBase))
        self.assertFalse(COMPAT.install(Owner()))
        self.assertIs(self.ChainBase.search_site_torrents, original)

    def test_custom_predicate_can_claim_a_non_legacy_site(self):
        owner = Owner()
        self.assertTrue(COMPAT.install(owner, predicate=lambda site: site.get("owned") is True))
        result = self.ChainBase().search_site_torrents({"domain": "custom.example", "owned": True}, "x")
        self.assertEqual(result, ["plugin-sync"])


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
        self.assertIn("foo%2Fbar", profiles[0]["domain"])
        self.assertEqual(profiles[0]["category"]["music"][0]["id"], "3000")
        self.assertEqual(
            INDEXERS.apply_indexer_selection(profiles, ["missing"], explicit=True),
            [],
        )


if __name__ == "__main__":
    unittest.main()
