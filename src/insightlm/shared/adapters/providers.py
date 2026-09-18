"""Classifying provider failures as transient or not.

Only some failures deserve another attempt. A 503 "model is overloaded" or a 429 rate
limit will likely succeed shortly; a 400 malformed request never will. Retrying
everything burns budget on requests that cannot succeed; retrying nothing loses
items to transient load -- five of thirty in one evaluation run, which is how this was
found.
"""

from __future__ import annotations


class TransientProviderError(Exception):
    """Raised in place of a provider error that is worth retrying."""


def classify_gemini_error(error: Exception) -> Exception:
    from google.genai import errors as genai_errors

    if isinstance(error, genai_errors.ServerError):
        return TransientProviderError(str(error))
    if isinstance(error, genai_errors.ClientError) and getattr(error, "code", None) == 429:
        return TransientProviderError(str(error))
    return error
