# ADR-0017: Provider SDKs directly, behind ports, not an LLM framework

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Three providers are used (Gemini for generation, judging and embeddings; Anthropic for
generation and judging; Voyage for embeddings) and each can be swapped by configuration.
Frameworks such as LangChain or LlamaIndex offer a common abstraction over them.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| LangChain / LlamaIndex | One interface; many integrations | A large, fast-moving dependency between us and every call; its abstractions (chains, retrievers, parsers) overlap with ours and hide what's sent; structured-output and error semantics vary by integration |
| **Each provider's own SDK, behind our ports** | We control exactly what is sent, parsed and retried; provider features (JSON schema, thinking budgets) used directly | We write and test one adapter per provider |

## Decision

Ports (`GroundedGenerator`, `CriterionJudge`, `Embedder`) with one thin adapter per
provider using its official SDK, plus an offline adapter for each that needs no key.

## Rationale

The failures that mattered in this project were all at the boundary with the provider,
and all needed direct control to see and fix: a confidence on the wrong scale and an
unparsable response silently becoming "absent" (Finding 13), thinking tokens starving
the answer (Finding 16), delimiter injection through the rendered prompt (Finding 14).
Each fix was a few lines in an adapter we own. Through a framework, each would have been
a question of what the framework does with our data.

## Consequences

- Swapping provider is a configuration change; the rest of the system sees a port.
- Every adapter has an offline twin, so the full test suite and CI run with no key.
- New provider features need an adapter change rather than a framework upgrade.

## Reversal

If the number of providers or model-facing features grew well beyond three adapters,
a framework's integrations could start to pay for themselves. The ports would stay; a
framework would sit behind one.
