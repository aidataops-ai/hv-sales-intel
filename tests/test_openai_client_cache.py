"""The AI modules must reuse one OpenAI client per process.

A client per call meant a fresh TCP+TLS handshake to api.openai.com on every
analyze/script/email/parse/qualify request and an abandoned connection pool
each time. These tests pin the caching contract *and* the property that makes
it safe under test: a patched `AsyncOpenAI` (or patched `settings`) must never
be served a client left behind by an earlier test.
"""

from unittest.mock import MagicMock, patch

import pytest

from src import analyzer, email_gen, icp_parser, lead_qualifier, scriptgen

ASYNC_MODULES = [analyzer, email_gen, icp_parser, scriptgen]


def _distinct_instances(cls_mock):
    """Make the patched class hand back a new object on every construction."""
    cls_mock.side_effect = lambda **kwargs: MagicMock()


@pytest.mark.parametrize("module", ASYNC_MODULES, ids=lambda m: m.__name__)
def test_client_is_built_once_and_reused(module):
    with patch.object(module, "AsyncOpenAI") as cls, patch.object(module, "settings") as s:
        _distinct_instances(cls)
        s.openai_api_key = "sk-a"
        first = module._get_client()
        second = module._get_client()

    assert first is second
    assert cls.call_count == 1


@pytest.mark.parametrize("module", ASYNC_MODULES, ids=lambda m: m.__name__)
def test_client_carries_an_explicit_timeout(module):
    with patch.object(module, "AsyncOpenAI") as cls, patch.object(module, "settings") as s:
        s.openai_api_key = "sk-a"
        module._get_client()

    assert cls.call_args.kwargs["timeout"] == 60.0
    assert cls.call_args.kwargs["api_key"] == "sk-a"


@pytest.mark.parametrize("module", ASYNC_MODULES, ids=lambda m: m.__name__)
def test_changing_the_api_key_rebuilds_the_client(module):
    with patch.object(module, "AsyncOpenAI") as cls, patch.object(module, "settings") as s:
        _distinct_instances(cls)
        s.openai_api_key = "sk-a"
        first = module._get_client()
        s.openai_api_key = "sk-b"
        second = module._get_client()

    assert first is not second
    assert cls.call_count == 2


@pytest.mark.parametrize("module", ASYNC_MODULES, ids=lambda m: m.__name__)
def test_a_patched_class_never_gets_an_earlier_patchs_client(module):
    """The cross-test leak guard: same API key, two different patches."""
    with patch.object(module, "settings") as s:
        s.openai_api_key = "sk-same"
        with patch.object(module, "AsyncOpenAI") as first_cls:
            first = module._get_client()
        with patch.object(module, "AsyncOpenAI") as second_cls:
            second = module._get_client()

    assert first is first_cls.return_value
    assert second is second_cls.return_value
    assert first is not second


@pytest.mark.parametrize("module", ASYNC_MODULES, ids=lambda m: m.__name__)
def test_reset_client_drops_the_cache(module):
    with patch.object(module, "AsyncOpenAI") as cls, patch.object(module, "settings") as s:
        _distinct_instances(cls)
        s.openai_api_key = "sk-a"
        first = module._get_client()
        module._reset_client()
        second = module._get_client()

    assert first is not second
    assert cls.call_count == 2


def test_qualifier_client_is_cached_and_keeps_the_long_timeout():
    """The qualifier's 300s ceiling is load-bearing — a batch runs for minutes."""
    calls = []

    def _fake(**kwargs):
        calls.append(kwargs)
        return MagicMock()

    with patch.object(lead_qualifier, "settings") as s:
        s.openai_api_key = "sk-q"
        with patch("openai.OpenAI", side_effect=_fake):
            first = lead_qualifier._client()
            second = lead_qualifier._client()

    assert first is second
    assert len(calls) == 1
    assert calls[0]["timeout"] == 300


def test_qualifier_rebuilds_on_key_change_and_on_reset():
    with patch.object(lead_qualifier, "settings") as s:
        with patch("openai.OpenAI", side_effect=lambda **kwargs: MagicMock()):
            s.openai_api_key = "sk-a"
            first = lead_qualifier._client()
            s.openai_api_key = "sk-b"
            second = lead_qualifier._client()
            lead_qualifier._reset_client()
            third = lead_qualifier._client()

    assert first is not second
    assert second is not third


def test_qualifier_still_refuses_to_build_without_a_key():
    with patch.object(lead_qualifier, "settings") as s:
        s.openai_api_key = ""
        with pytest.raises(RuntimeError):
            lead_qualifier._client()
