# _*_ coding: utf-8 _*_
"""Lifecycle-safe per-site bridge for the current MoviePilot V3 search API.

The current host's two per-site search boundaries still execute system
modules.  The plugin owns only its virtual profiles, so the bridge redirects
those calls and leaves the host's global-plugin, ordinary-site and page-size
paths untouched.
"""

import functools
import importlib
import inspect
import threading
import weakref
from collections.abc import Mapping
from typing import Any, Callable, Optional
from urllib.parse import urlsplit


# This attribute name is deliberately stable across module reloads.  State is
# attached to the host class, so loading a new plugin module can retire an
# incompatible wrapper before installing a fresh one.
_STATE_ATTR = "__jackett_extend_host_compat_state__"
_BRIDGE_ABI = 2
_BRIDGE_TOKEN = object()
_METHODS = (
    "search_site_torrents",
    "async_search_site_torrents",
)

_STATE_INIT_LOCK = threading.RLock()
_MISSING = object()


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


def _state_lock(state: Mapping):
    """Return the state lock, adding one when migrating an older state."""
    lock = state.get("lock")
    if lock is not None and hasattr(lock, "__enter__"):
        return lock
    # Older V3 snapshots had no lock.  The short initialization section is
    # guarded by a module lock; all current wrappers use the published lock.
    with _STATE_INIT_LOCK:
        lock = state.get("lock")
        if lock is None or not hasattr(lock, "__enter__"):
            lock = threading.RLock()
            try:
                state["lock"] = lock
            except (TypeError, AttributeError):
                return _STATE_INIT_LOCK
    return lock


def _call_predicate(predicate: Callable, site: Mapping, domain: str):
    """Apply the current virtual-site predicate contract."""
    return predicate(site, domain)


def _owner_for_site(state: Mapping, site: object) -> Any:
    """Return a generation-validated owner for one virtual-site decision.

    Predicate execution is intentionally outside the lock.  The generation
    check after it prevents a reload/install that occurs during the predicate
    from dispatching into the owner captured before that change.
    """
    if not isinstance(site, Mapping):
        # In particular, preserve the host's global ``site={}`` plugin route.
        return None
    try:
        if not site:
            return None
    except Exception:
        return None
    try:
        domain = _normalise_domain(site)
    except Exception:
        return None

    lock = _state_lock(state)
    with lock:
        if not state.get("active", True):
            return None
        owner = _owner_from_state(state)
        generation = state.get("generation", 0)
        predicate = _predicate_from_state(state)
    if owner is None:
        return None
    if predicate is None:
        # Read the current owner on each request so reloads cannot retain a
        # stopped instance through a bound method.
        try:
            candidate = getattr(owner, "_is_virtual_site", None)
        except Exception:
            return None
        if callable(candidate):
            predicate = candidate
    if predicate is None:
        return None
    try:
        owned = bool(_call_predicate(predicate, site, domain))
    except Exception:
        return None
    if not owned:
        return None

    with lock:
        if not state.get("active", True):
            return None
        if state.get("generation", 0) != generation:
            return None
        if _owner_from_state(state) is not owner:
            return None
    return owner


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
        site = _site_from_call(args, kwargs)
        owner = _owner_for_site(state, site)
        if owner is not None:
            try:
                return _call_owner(owner, "search_torrents", args[1:], kwargs)
            except Exception:
                # A plugin module must not break the host's ordinary search
                # chain.  BaseException (including cancellation) is allowed
                # through deliberately.
                pass
        return original(*args, **kwargs)

    wrapper.__jackett_extend_wrapper__ = True
    return wrapper


def _make_async_wrapper(original, state):
    @functools.wraps(original)
    async def wrapper(*args, **kwargs):
        site = _site_from_call(args, kwargs)
        owner = _owner_for_site(state, site)
        if owner is not None:
            try:
                return await _call_owner_async(owner, "async_search_torrents", args[1:], kwargs)
            except Exception:
                pass
        result = original(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    wrapper.__jackett_extend_wrapper__ = True
    return wrapper


def _state_method_names(state: Mapping):
    """Return every method recorded by this or an older bridge state."""
    names = []
    for key in ("originals", "wrappers", "defined_here"):
        values = state.get(key) or {}
        if not isinstance(values, Mapping):
            continue
        for name in values:
            if isinstance(name, str) and name not in names:
                names.append(name)
    return tuple(names)


def _current_descriptor(chain_base: type, name: str):
    try:
        return inspect.getattr_static(chain_base, name)
    except AttributeError:
        return _MISSING


def _restore(chain_base: type, state: Mapping) -> bool:
    """Retire a state and restore only attributes still owned by its bridge.

    The method list comes from the state itself so a new module can safely
    migrate a prior three-method snapshot (including its page-size wrapper).
    A third-party replacement is left untouched rather than being overwritten.
    """
    lock = _state_lock(state)
    with lock:
        state["active"] = False
        state["generation"] = int(state.get("generation", 0)) + 1
        originals = state.get("originals") or {}
        wrappers = state.get("wrappers") or {}
        defined_here = state.get("defined_here") or {}
        ok = True
        for name in _state_method_names(state):
            if name not in originals or not isinstance(wrappers, Mapping):
                continue
            expected = wrappers.get(name, _MISSING)
            if expected is _MISSING:
                continue
            # Never replace a method that was changed after bridge install.
            if _current_descriptor(chain_base, name) is not expected:
                continue
            try:
                if defined_here.get(name, name in getattr(chain_base, "__dict__", {})):
                    setattr(chain_base, name, originals[name])
                elif name in getattr(chain_base, "__dict__", {}):
                    # The original came from a base class; removing our
                    # subclass assignment restores normal lookup exactly.
                    delattr(chain_base, name)
            except Exception:
                ok = False
        if getattr(chain_base, _STATE_ATTR, None) is state:
            try:
                delattr(chain_base, _STATE_ATTR)
            except Exception:
                ok = False
        return ok


def _state_is_current(chain_base: type, state: Mapping) -> bool:
    if (
        state.get("abi") != _BRIDGE_ABI
        or state.get("module_token") is not _BRIDGE_TOKEN
        or tuple(state.get("methods") or ()) != _METHODS
        or not state.get("active", True)
    ):
        return False
    wrappers = state.get("wrappers")
    if not isinstance(wrappers, Mapping):
        return False
    return all(
        name in wrappers
        and _current_descriptor(chain_base, name) is wrappers[name]
        for name in _METHODS
    )


def install(plugin: object, predicate: Optional[Callable] = None) -> bool:
    """Install/update the per-site wrappers for the current plugin owner."""
    chain_base = _find_chain_base()
    if chain_base is None:
        return False

    # Installation/migration mutates class attributes and is serialized only
    # for that short publication phase.  No owner code runs while held.
    with _STATE_INIT_LOCK:
        state = getattr(chain_base, _STATE_ATTR, None)
        if isinstance(state, dict):
            if not _state_is_current(chain_base, state):
                if not _restore(chain_base, state):
                    return False
                state = getattr(chain_base, _STATE_ATTR, None)
            if isinstance(state, dict):
                lock = _state_lock(state)
                with lock:
                    current_owner = _owner_from_state(state)
                    if current_owner is plugin:
                        if predicate is not None:
                            _set_predicate(state, predicate, plugin)
                        return True
                    # A new generation takes over the existing current bridge;
                    # its wrappers remain stable while owner state is swapped.
                    _set_owner(state, plugin)
                    state["predicate_ref"] = None
                    state["predicate"] = None
                    if predicate is not None:
                        _set_predicate(state, predicate, plugin)
                return True

        if not all(callable(getattr(chain_base, name, None)) for name in _METHODS):
            return False
        state = {
            "abi": _BRIDGE_ABI,
            "module_token": _BRIDGE_TOKEN,
            "methods": _METHODS,
            "active": True,
            "generation": 0,
            "lock": threading.RLock(),
            "owner_ref": None,
            "owner": None,
            "predicate_ref": None,
            "predicate": None,
            "originals": {},
            "defined_here": {},
            "wrappers": {},
        }
        lock = state["lock"]
        with lock:
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
    state["generation"] = int(state.get("generation", 0)) + 1


def _set_predicate(state: dict, predicate: Callable, plugin: object) -> None:
    # A bound predicate on the plugin would keep the old instance alive across
    # reload.  The wrapper already calls the current owner's predicate, so omit
    # that redundant strong reference.  Standalone predicates are weakly held
    # when possible and are otherwise kept as small test/user callables.
    before = (state.get("predicate_ref"), state.get("predicate"))
    if inspect.ismethod(predicate) and getattr(predicate, "__self__", None) is plugin:
        state["predicate_ref"] = None
        state["predicate"] = None
    else:
        # Plain functions/lambdas are intentionally retained: a caller
        # commonly passes an inline predicate and a weak reference would
        # disappear before the first host search.  A closure that captures the
        # old plugin is omitted to avoid turning that optional hook into a
        # stale-owner root.
        closure = getattr(predicate, "__closure__", None) or ()
        if any(getattr(cell, "cell_contents", None) is plugin for cell in closure):
            state["predicate_ref"] = None
            state["predicate"] = None
        else:
            state["predicate_ref"] = None
            state["predicate"] = predicate
    after = (state.get("predicate_ref"), state.get("predicate"))
    if before != after:
        state["generation"] = int(state.get("generation", 0)) + 1


def uninstall(plugin: object) -> bool:
    """Restore originals only when ``plugin`` is the current owner."""
    chain_base = _find_chain_base()
    if chain_base is None:
        return False
    with _STATE_INIT_LOCK:
        state = getattr(chain_base, _STATE_ATTR, None)
        if not isinstance(state, Mapping):
            return False
        lock = _state_lock(state)
        with lock:
            if getattr(chain_base, _STATE_ATTR, None) is not state:
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
    lock = _state_lock(state)
    with lock:
        owner = _owner_from_state(state)
        active = bool(state.get("active", True))
        return {
            "available": True,
            "installed": active,
            "owner_alive": active and owner is not None,
            "methods": tuple(_METHODS),
        }


is_installed = lambda: bool(status().get("installed"))
