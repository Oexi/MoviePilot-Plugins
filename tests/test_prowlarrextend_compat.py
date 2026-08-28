import asyncio
import inspect
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JACKETT_PATH = ROOT / "plugins.v3" / "jackettextend" / "_host_compat.py"
PROWLARR_PATH = ROOT / "plugins.v3" / "prowlarrextend" / "_host_compat.py"


def load_compat(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Owner:
    def __init__(self, value, plugin_name=None):
        self.value = value
        self.plugin_name = plugin_name or value
        self.sync_calls = 0
        self.async_calls = 0

    def search_torrents(self, *args, **kwargs):
        self.sync_calls += 1
        return [self.value]

    async def async_search_torrents(self, *args, **kwargs):
        self.async_calls += 1
        return [self.value]


class ProwlarrCompatContractTest(unittest.TestCase):
    @staticmethod
    def _predicate(plugin_name, prefix):
        def owns(site, domain):
            markers = {
                str(site.get("plugin") or "").lower(),
                str(site.get("parser") or "").lower(),
            }
            if any(markers):
                return plugin_name.lower() in markers
            return domain.lower().startswith(prefix)
        return owns

    def _exercise_load_order(self, first_name):
        jackett_compat = load_compat(JACKETT_PATH, f"jackett_compat_{first_name}")
        prowlarr_compat = load_compat(PROWLARR_PATH, f"prowlarr_compat_{first_name}")
        calls = []

        class ChainBase:
            def search_site_torrents(self, site, keyword, *args, **kwargs):
                calls.append(("sync", site, keyword))
                return ["host"]

            async def async_search_site_torrents(self, site, keyword, *args, **kwargs):
                calls.append(("async", site, keyword))
                return ["host"]

        app = types.ModuleType("app")
        chain = types.ModuleType("app.chain")
        chain.ChainBase = ChainBase
        app.chain = chain
        previous = {"app": sys.modules.get("app"), "app.chain": sys.modules.get("app.chain")}
        sys.modules.update({"app": app, "app.chain": chain})
        try:
            jackett = Owner("jackett", "JackettExtend")
            prowlarr = Owner("prowlarr", "ProwlarrExtend")
            installs = {
                "jackett": lambda: jackett_compat.install(
                    jackett,
                    predicate=self._predicate("JackettExtend", "jackett_extend."),
                ),
                "prowlarr": lambda: prowlarr_compat.install(
                    prowlarr,
                    predicate=self._predicate("ProwlarrExtend", "prowlarr_extend."),
                    owner_key="prowlarrextend",
                ),
            }
            second_name = "prowlarr" if first_name == "jackett" else "jackett"
            originals = {
                name: inspect.getattr_static(ChainBase, name)
                for name in ("search_site_torrents", "async_search_site_torrents")
            }
            self.assertTrue(installs[first_name]())
            wrapped = {
                name: inspect.getattr_static(ChainBase, name) for name in originals
            }
            self.assertTrue(installs[second_name]())
            # Independently loaded helpers must share, never stack, wrappers.
            for name in wrapped:
                self.assertIs(inspect.getattr_static(ChainBase, name), wrapped[name])

            chain_instance = ChainBase()
            self.assertEqual(
                chain_instance.search_site_torrents({"domain": "jackett_extend.nyaa"}, "x"),
                ["jackett"],
            )
            self.assertEqual(
                chain_instance.search_site_torrents({"domain": "prowlarr_extend.7"}, "x"),
                ["prowlarr"],
            )
            # Explicit ownership wins over a conflicting historical domain.
            self.assertEqual(
                chain_instance.search_site_torrents({
                    "domain": "jackett_extend.looks-like-jackett",
                    "plugin": "ProwlarrExtend",
                }, "x"),
                ["prowlarr"],
            )
            self.assertEqual(
                chain_instance.search_site_torrents({"domain": "ordinary.example"}, "x"),
                ["host"],
            )
            self.assertEqual(chain_instance.search_site_torrents({}, "global"), ["host"])
            self.assertEqual(
                asyncio.run(chain_instance.async_search_site_torrents(
                    {"domain": "prowlarr_extend.7"}, "x"
                )),
                ["prowlarr"],
            )

            # Disabling either first leaves the other plugin active.
            if first_name == "jackett":
                self.assertTrue(jackett_compat.uninstall(jackett))
                remaining_site = {"domain": "prowlarr_extend.7"}
                remaining_result = ["prowlarr"]
                last_uninstall = lambda: prowlarr_compat.uninstall(
                    prowlarr, owner_key="prowlarrextend"
                )
            else:
                self.assertTrue(prowlarr_compat.uninstall(
                    prowlarr, owner_key="prowlarrextend"
                ))
                remaining_site = {"domain": "jackett_extend.nyaa"}
                remaining_result = ["jackett"]
                last_uninstall = lambda: jackett_compat.uninstall(jackett)
            self.assertEqual(
                chain_instance.search_site_torrents(remaining_site, "x"),
                remaining_result,
            )
            self.assertTrue(last_uninstall())
            for name, original in originals.items():
                self.assertIs(inspect.getattr_static(ChainBase, name), original)
        finally:
            state = getattr(ChainBase, jackett_compat._STATE_ATTR, None)
            if isinstance(state, dict):
                for key, record in list((state.get("owners") or {}).items()):
                    owner = jackett_compat._owner_from_record(record)
                    if owner is not None:
                        jackett_compat.uninstall(owner, owner_key=key)
            for name, value in previous.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value

    def test_both_plugins_coexist_in_either_load_order_and_disable_order(self):
        for first_name in ("jackett", "prowlarr"):
            with self.subTest(first=first_name):
                self._exercise_load_order(first_name)

    def test_same_key_reload_replaces_only_prowlarr_and_stale_owner_is_safe(self):
        first_compat = load_compat(PROWLARR_PATH, "prowlarr_compat_reload_first")
        second_compat = load_compat(PROWLARR_PATH, "prowlarr_compat_reload_second")
        jackett_compat = load_compat(JACKETT_PATH, "jackett_compat_reload")

        class ChainBase:
            def search_site_torrents(self, site, keyword, *args, **kwargs):
                return ["host"]

            async def async_search_site_torrents(self, site, keyword, *args, **kwargs):
                return ["host"]

        app = types.ModuleType("app")
        chain = types.ModuleType("app.chain")
        chain.ChainBase = ChainBase
        app.chain = chain
        previous = {"app": sys.modules.get("app"), "app.chain": sys.modules.get("app.chain")}
        sys.modules.update({"app": app, "app.chain": chain})
        try:
            jackett = Owner("jackett", "JackettExtend")
            old = Owner("old-prowlarr", "ProwlarrExtend")
            new = Owner("new-prowlarr", "ProwlarrExtend")
            self.assertTrue(jackett_compat.install(
                jackett, predicate=self._predicate("JackettExtend", "jackett_extend.")
            ))
            self.assertTrue(first_compat.install(
                old,
                predicate=self._predicate("ProwlarrExtend", "prowlarr_extend."),
                owner_key="prowlarrextend",
            ))
            wrapped = inspect.getattr_static(ChainBase, "search_site_torrents")
            self.assertTrue(second_compat.install(
                new,
                predicate=self._predicate("ProwlarrExtend", "prowlarr_extend."),
                owner_key="prowlarrextend",
            ))
            self.assertIs(inspect.getattr_static(ChainBase, "search_site_torrents"), wrapped)
            self.assertFalse(first_compat.uninstall(old, owner_key="prowlarrextend"))
            instance = ChainBase()
            self.assertEqual(
                instance.search_site_torrents({"domain": "prowlarr_extend.7"}, "x"),
                ["new-prowlarr"],
            )
            self.assertEqual(
                instance.search_site_torrents({"domain": "jackett_extend.nyaa"}, "x"),
                ["jackett"],
            )
            self.assertTrue(second_compat.uninstall(new, owner_key="prowlarrextend"))
            self.assertEqual(
                instance.search_site_torrents({"domain": "jackett_extend.nyaa"}, "x"),
                ["jackett"],
            )
            self.assertTrue(jackett_compat.uninstall(jackett))
        finally:
            state = getattr(ChainBase, jackett_compat._STATE_ATTR, None)
            if isinstance(state, dict):
                for key, record in list((state.get("owners") or {}).items()):
                    owner = jackett_compat._owner_from_record(record)
                    if owner is not None:
                        jackett_compat.uninstall(owner, owner_key=key)
            for name, value in previous.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value


if __name__ == "__main__":
    unittest.main()
