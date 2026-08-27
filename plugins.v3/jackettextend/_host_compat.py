# _*_ coding: utf-8 _*_
"""Lifecycle-safe per-site bridge for the current MoviePilot V3 search API.

The current host exposes three ChainBase methods for per-site search.  The
plugin owns only its virtual profiles, so the bridge redirects those calls and
leaves the host's global-plugin and ordinary-site paths untouched.
"""

import functools
import importlib
import inspect
import weakref
from collections.abc import Mapping
from typing import Any, Callable, Optional
from urllib.parse import urlsplit


# This attribute name is deliberately stable across module reloads.  State is
# attached to the host class, so loading a new plugin module updates an existing
# wrapper instead of stacking another wrapper around it.
_STATE_ATTR = "__jackett_extend_host_compat_state__"
_METHODS = (
    "search_site_torrents",
    "async_search_site_torrents",
    "get_search_page_size",
)


def _find_chain_base() -> Optional[type]:
    """Lazily resolve the current host boundary."""
    try:
        module = importlib.import_module("app.chain")
    except (ImportError, ModuleNotFoundError):
        return None
    chain_base = getattr(module, "ChainBase", None)
    return chain_base if inspect.isclass(chain_base) else None


def _normalise_domain(site: Mapping) -> str:
    value = str(site.get("domain") or "").strip()
    if value.lower().startswith(("http://", "https://")):
        try:
            value = urlsplit(value).hostname or value
        except ValueError:
            pass
    return value


def _owner_from_state(state: Mapping) -> Any:
    owner_ref = state.get("owner_ref")
    if callable(owner_ref):
        try:
            return owner_ref()
        except Exception:
            return None
    # Only used for deliberately non-weak-referenceable test doubles.  Normal
    # plugin instances always use a weak reference and cannot be retained by a
    # stale wrapper after reload/stop.
    return state.get("owner")


def _weak_owner(plugin: object):
    try:
        return weakref.ref(plugin)
    except TypeError:
        return None


def _predicate_from_state(state: Mapping) -> Optional[Callable]:
    predicate_ref = state.get("predicate_ref")
    if callable(predicate_ref):
        try:
            return predicate_ref()
        except Exception:
            return None
    predicate = state.get("predicate")
    return predicate if callable(predicate) else None


def _call_predicate(predicate: Callable, site: Mapping, domain: str):
    """Apply the current virtual-site predicate contract."""
    return predicate(site, domain)


def _site_owned_by_plugin(state: Mapping, site: object) -> bool:
    """Return whether the current plugin predicate owns this site."""
    if not isinstance(site, Mapping) or not site:
        # In particular, preserve the host's global ``site={}`` plugin route.
        return False
    owner = _owner_from_state(state)
    if owner is None:
        return False
    domain = _normalise_domain(site)
    predicate = _predicate_from_state(state)
    if predicate is None:
        # Read the current owner on each request so reloads cannot retain a
        # stopped instance through a bound method.
        candidate = getattr(owner, "_is_virtual_site", None)
        if callable(candidate):
            predicate = candidate
    if predicate is None:
        return False
    try:
        return bool(_call_predicate(predicate, site, domain))
    except Exception:
        return False


def _call_owner(owner: object, method: str, args: tuple, kwargs: dict):
    return getattr(owner, method)(*args, **kwargs)


async def _call_owner_async(owner: object, method: str, args: tuple, kwargs: dict):
    result = _call_owner(owner, method, args, kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _site_from_call(args: tuple, kwargs: dict):
    if "site" in kwargs:
        return kwargs.get("site")
    # The first positional argument is ChainBase; site follows it.
    return args[1] if len(args) > 1 else None


def _make_sync_wrapper(original, state):
    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        owner = _owner_from_state(state)
        site = _site_from_call(args, kwargs)
        if owner is not None and _site_owned_by_plugin(state, site):
            return _call_owner(owner, "search_torrents", args[1:], kwargs)
        return original(*args, **kwargs)

    wrapper.__jackett_extend_wrapper__ = True
    return wrapper


def _make_async_wrapper(original, state):
    @functools.wraps(original)
    async def wrapper(*args, **kwargs):
        owner = _owner_from_state(state)
        site = _site_from_call(args, kwargs)
        if owner is not None and _site_owned_by_plugin(state, site):
            return await _call_owner_async(owner, "async_search_torrents", args[1:], kwargs)
        result = original(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    wrapper.__jackett_extend_wrapper__ = True
    return wrapper


def _make_page_size_wrapper(original, state):
    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        owner = _owner_from_state(state)
        site = _site_from_call(args, kwargs)
        if owner is not None and _site_owned_by_plugin(state, site):
            return _call_owner(owner, "get_search_page_size", args[1:], kwargs)
        return original(*args, **kwargs)

    wrapper.__jackett_extend_wrapper__ = True
    return wrapper


def _restore(chain_base: type, state: Mapping) -> bool:
    originals = state.get("originals") or {}
    defined_here = state.get("defined_here") or {}
    try:
        for name in _METHODS:
            if name not in originals:
                continue
            if defined_here.get(name):
                setattr(chain_base, name, originals[name])
            else:
                # The original came from a base class; removing our subclass
                # assignment restores normal attribute lookup exactly.
                delattr(chain_base, name)
        if getattr(chain_base, _STATE_ATTR, None) is state:
            delattr(chain_base, _STATE_ATTR)
        return True
    except Exception:
        return False


def install(plugin: object, predicate: Optional[Callable] = None) -> bool:
    """Install/update the per-site wrappers for the current plugin owner."""
    chain_base = _find_chain_base()
    if chain_base is None:
        return False

    state = getattr(chain_base, _STATE_ATTR, None)
    if isinstance(state, dict):
        current_owner = _owner_from_state(state)
        if current_owner is plugin:
            if predicate is not None:
                _set_predicate(state, predicate, plugin)
            return True
        # Module reload/host reload: reuse the already wrapped class and only
        # publish the newest live owner.  No old closure or descriptor is added.
        _set_owner(state, plugin)
        # Do not carry an optional predicate that may close over the old
        # instance into the new generation.  The live owner's predicate is
        # consulted dynamically when no replacement hook is supplied.
        state["predicate_ref"] = None
        state["predicate"] = None
        if predicate is not None:
            _set_predicate(state, predicate, plugin)
        return True

    if not all(callable(getattr(chain_base, name, None)) for name in _METHODS):
        return False
    state = {
        "version": 1,
        "owner_ref": None,
        "owner": None,
        "predicate_ref": None,
        "predicate": None,
        "originals": {},
        "defined_here": {},
        "wrappers": {},
    }
    _set_owner(state, plugin)
    if predicate is not None:
        _set_predicate(state, predicate, plugin)
    try:
        for name in _METHODS:
            state["originals"][name] = inspect.getattr_static(chain_base, name)
            state["defined_here"][name] = name in getattr(chain_base, "__dict__", {})
        state["wrappers"] = {
            "search_site_torrents": _make_sync_wrapper(
                state["originals"]["search_site_torrents"], state),
            "async_search_site_torrents": _make_async_wrapper(
                state["originals"]["async_search_site_torrents"], state),
            "get_search_page_size": _make_page_size_wrapper(
                state["originals"]["get_search_page_size"], state),
        }
        for name, wrapper in state["wrappers"].items():
            setattr(chain_base, name, wrapper)
        setattr(chain_base, _STATE_ATTR, state)
        return True
    except Exception:
        _restore(chain_base, state)
        return False


def _set_owner(state: dict, plugin: object) -> None:
    owner_ref = _weak_owner(plugin)
    state["owner_ref"] = owner_ref
    state["owner"] = None if owner_ref is not None else plugin


def _set_predicate(state: dict, predicate: Callable, plugin: object) -> None:
    # A bound predicate on the plugin would keep the old instance alive across
    # reload.  The wrapper already calls the current owner's predicate, so omit
    # that redundant strong reference.  Standalone predicates are weakly held
    # when possible and are otherwise kept as small test/user callables.
    if inspect.ismethod(predicate) and getattr(predicate, "__self__", None) is plugin:
        state["predicate_ref"] = None
        state["predicate"] = None
        return
    # Plain functions/lambdas are intentionally retained: a caller commonly
    # passes an inline predicate and a weak reference would disappear before
    # the first host search.  A closure that captures the old plugin is
    # omitted to avoid turning that optional hook into a stale-owner root.
    closure = getattr(predicate, "__closure__", None) or ()
    if any(getattr(cell, "cell_contents", None) is plugin for cell in closure):
        state["predicate_ref"] = None
        state["predicate"] = None
    else:
        state["predicate_ref"] = None
        state["predicate"] = predicate


def uninstall(plugin: object) -> bool:
    """Restore originals only when ``plugin`` is the current owner."""
    chain_base = _find_chain_base()
    if chain_base is None:
        return False
    state = getattr(chain_base, _STATE_ATTR, None)
    if not isinstance(state, Mapping):
        return False
    if _owner_from_state(state) is not plugin:
        return False
    return _restore(chain_base, state)


def status() -> dict:
    """Return small, non-sensitive diagnostics for isolated tests."""
    chain_base = _find_chain_base()
    if chain_base is None:
        return {"available": False, "installed": False, "owner_alive": False}
    state = getattr(chain_base, _STATE_ATTR, None)
    if not isinstance(state, Mapping):
        return {
            "available": True,
            "installed": False,
            "owner_alive": False,
        }
    owner = _owner_from_state(state)
    return {
        "available": True,
        "installed": True,
        "owner_alive": owner is not None,
        "methods": tuple(_METHODS),
    }


is_installed = lambda: bool(status().get("installed"))
