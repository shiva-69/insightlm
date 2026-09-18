"""Choosing a generator.

All providers satisfy one port, so the choice must change nothing downstream -- but
choosing *wrongly and silently* is the failure that matters. An evaluation run that
quietly answers with the extractive fallback because a key was missing produces
numbers that look fine and mean nothing.
"""

from __future__ import annotations

import pytest

from insightlm.bootstrap import _build_generator
from insightlm.retrieval.adapters.extractive_generator import ExtractiveGenerator
from insightlm.settings import Settings
from insightlm.shared.domain.errors import InvariantViolationError


def settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        jwt_secret="test-secret-not-used-anywhere-real",  # type: ignore[arg-type]
        **overrides,  # type: ignore[arg-type]
    )


def test_no_keys_falls_back_to_the_extractive_generator() -> None:
    assert isinstance(_build_generator(settings()), ExtractiveGenerator)


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_key_counts_as_absent(blank: str) -> None:
    """Regression: compose's `${VAR:-}` sets an empty string, not an unset variable.

    An empty string is not None, so the provider adapter was constructed with a blank
    key and the service died on startup instead of using the offline path it was
    meant to.
    """
    resolved = settings(anthropic_api_key=blank, voyage_api_key=blank)
    assert resolved.anthropic_api_key is None
    assert resolved.voyage_api_key is None
    assert isinstance(_build_generator(resolved), ExtractiveGenerator)


def test_auto_prefers_anthropic_when_its_key_is_present() -> None:
    generator = _build_generator(
        settings(anthropic_api_key="sk-test", gemini_api_key="gm-test")
    )
    assert type(generator).__name__ == "AnthropicGroundedGenerator"


def test_auto_uses_gemini_when_only_its_key_is_present() -> None:
    generator = _build_generator(settings(gemini_api_key="gm-test"))
    assert type(generator).__name__ == "GeminiGroundedGenerator"


def test_naming_a_provider_overrides_the_preference_order() -> None:
    generator = _build_generator(
        settings(
            generation_provider="gemini",
            anthropic_api_key="sk-test",
            gemini_api_key="gm-test",
        )
    )
    assert type(generator).__name__ == "GeminiGroundedGenerator"


@pytest.mark.parametrize("provider", ["anthropic", "gemini"])
def test_naming_a_provider_without_its_key_fails_loudly(provider: str) -> None:
    """The point of naming one explicitly.

    Under `auto` a missing key is a downgrade; under an explicit provider it is a
    configuration error, and an evaluation run should refuse to start rather than
    produce results from the wrong engine.
    """
    with pytest.raises(InvariantViolationError, match="API key is not configured"):
        _build_generator(settings(generation_provider=provider))


def test_offline_can_be_selected_even_with_keys_present() -> None:
    """So an ablation can measure the extractive baseline without unsetting anything."""
    generator = _build_generator(
        settings(generation_provider="offline", anthropic_api_key="sk-test")
    )
    assert isinstance(generator, ExtractiveGenerator)


def test_every_provider_satisfies_the_same_port() -> None:
    """Structurally, not via isinstance.

    `GroundedGenerator` is a Protocol and is deliberately not `@runtime_checkable`:
    an isinstance check against a Protocol only verifies that method *names* exist,
    which would pass for an object whose `generate` takes entirely the wrong
    arguments. mypy already proves conformance properly at the point `_build_generator`
    declares its return type; what is worth asserting here is that the signature the
    call site depends on is actually present on all three.
    """
    import inspect

    for resolved in (
        settings(),
        settings(anthropic_api_key="sk-test"),
        settings(gemini_api_key="gm-test"),
    ):
        generate = _build_generator(resolved).generate
        assert inspect.iscoroutinefunction(generate)
        parameters = inspect.signature(generate).parameters
        assert list(parameters)[:2] == ["question", "evidence"]
        assert parameters["feedback"].kind is inspect.Parameter.KEYWORD_ONLY
