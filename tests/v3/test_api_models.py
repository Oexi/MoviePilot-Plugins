"""Contract tests for the V3 plugin API response models."""

from importlib import import_module

import pytest

MODEL_CASES = (
    (
        "app.plugins.jackettextend._api_models",
        "JackettStatusResponse",
        "JackettTestResponse",
    ),
    (
        "app.plugins.prowlarrextend._api_models",
        "ProwlarrStatusResponse",
        "ProwlarrTestResponse",
    ),
)


def _status_payload() -> dict:
    """Return a representative raw status payload without credentials."""
    return {
        "enabled": True,
        "configured": True,
        "connected": True,
        "sync": {
            "fetch_ok": True,
            "ready": True,
            "last_ok": False,
            "last_at": 1720000000.25,
        },
        "indexer_count": 4,
        "selected_count": 2,
        "last_error": "sync_timeout",
        "last_error_at": 1720000001.5,
        "last_search_error": "torznab_error",
        "last_search_error_at": 1720000002.75,
        "probe_error": None,
        "probe_error_at": None,
    }


@pytest.mark.parametrize(
    ("module_name", "status_name", "test_name"),
    MODEL_CASES,
)
def test_status_model_preserves_the_bare_nested_payload(
    module_name: str,
    status_name: str,
    test_name: str,
) -> None:
    """The status model has exactly the fields emitted by ``api_status``."""
    del test_name
    module = import_module(module_name)
    model = getattr(module, status_name)
    payload = _status_payload()

    parsed = model.model_validate(payload)

    assert parsed.model_dump() == payload
    assert list(model.model_fields) == [
        "enabled",
        "configured",
        "connected",
        "sync",
        "indexer_count",
        "selected_count",
        "last_error",
        "last_error_at",
        "last_search_error",
        "last_search_error_at",
        "probe_error",
        "probe_error_at",
    ]
    assert "success" not in model.model_fields
    assert "message" not in model.model_fields
    assert "data" not in model.model_fields


@pytest.mark.parametrize(
    ("module_name", "status_name", "test_name"),
    MODEL_CASES,
)
def test_status_model_accepts_uninitialized_none_values(
    module_name: str,
    status_name: str,
    test_name: str,
) -> None:
    """Unset diagnostics remain valid before the first sync or probe."""
    del test_name
    module = import_module(module_name)
    model = getattr(module, status_name)
    payload = _status_payload()
    payload["configured"] = False
    payload["connected"] = False
    payload["sync"] = {
        "fetch_ok": False,
        "ready": False,
        "last_ok": False,
        "last_at": None,
    }
    for field in (
        "last_error",
        "last_error_at",
        "last_search_error",
        "last_search_error_at",
        "probe_error",
        "probe_error_at",
    ):
        payload[field] = None

    parsed = model.model_validate(payload)

    assert parsed.model_dump() == payload
    assert parsed.sync.last_at is None


@pytest.mark.parametrize(
    ("module_name", "status_name", "test_name"),
    MODEL_CASES,
)
def test_test_model_only_adds_ok_to_the_status_payload(
    module_name: str,
    status_name: str,
    test_name: str,
) -> None:
    """The connectivity probe keeps the status shape and adds only ``ok``."""
    module = import_module(module_name)
    status_model = getattr(module, status_name)
    test_model = getattr(module, test_name)
    payload = _status_payload()
    payload["ok"] = True

    parsed = test_model.model_validate(payload)

    assert parsed.model_dump() == payload
    assert list(test_model.model_fields) == [*status_model.model_fields, "ok"]
    assert set(test_model.model_fields) == set(status_model.model_fields) | {"ok"}


@pytest.mark.parametrize(
    ("module_name", "status_name", "test_name"),
    MODEL_CASES,
)
def test_models_do_not_serialize_secret_or_envelope_fields(
    module_name: str,
    status_name: str,
    test_name: str,
) -> None:
    """Unknown credential-like input cannot become part of the API payload."""
    module = import_module(module_name)
    status_model = getattr(module, status_name)
    test_model = getattr(module, test_name)
    status_payload = _status_payload()
    status_payload.update(
        {
            "host": "https://user:password@example.invalid",
            "api_key": "secret-key",
            "password": "secret-password",
            "success": True,
            "message": "secret message",
            "data": {"secret": True},
        }
    )

    status_dump = status_model.model_validate(status_payload).model_dump()
    test_payload = dict(status_payload, ok=False)
    test_dump = test_model.model_validate(test_payload).model_dump()

    for output in (status_dump, test_dump):
        assert set(output) <= {
            "enabled",
            "configured",
            "connected",
            "sync",
            "indexer_count",
            "selected_count",
            "last_error",
            "last_error_at",
            "last_search_error",
            "last_search_error_at",
            "probe_error",
            "probe_error_at",
            "ok",
        }
        rendered = repr(output)
        assert "secret" not in rendered
        assert "password" not in rendered
        assert "user:" not in rendered
