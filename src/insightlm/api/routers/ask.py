"""Grounded question answering."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from insightlm.api.deps import ActorDep, ContainerDep
from insightlm.api.schemas import (
    AnswerResponse,
    AskRequest,
    CitationResponse,
    ClaimResponse,
)
from insightlm.retrieval.application.answer import (
    AnswerEvent,
    AnswerOutput,
    AnswerRequest,
    ClaimReady,
    EvidenceFound,
    Withdrawn,
)
from insightlm.retrieval.domain.evidence import RetrievedSpan
from insightlm.shared.domain.errors import DomainError
from insightlm.shared.domain.ids import DocumentId
from insightlm.shared.observability.logging import get_logger

_logger = get_logger(__name__)

router = APIRouter(tags=["ask"])


def _request(request: AskRequest) -> AnswerRequest:
    return AnswerRequest(
        question=request.question,
        top_k=request.top_k,
        document_ids=(
            tuple(DocumentId(d) for d in request.document_ids) if request.document_ids else None
        ),
    )


def _rects(span: RetrievedSpan | None) -> list[dict[str, float | int]]:
    return [
        {"page": r.page, "x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1}
        for r in (span.span.rects if span else ())
    ]


def _passage(span: RetrievedSpan) -> CitationResponse:
    """A retrieved passage, in the shape of a citation, so the reader can open it before
    any claim cites it."""
    return CitationResponse(
        label=span.label,
        chunk_id=span.chunk_id,
        document_id=span.document_id,
        document_title=span.document_title,
        page=span.page,
        char_start=span.span.char_start,
        char_end=span.span.char_end,
        quoted_text=span.text,
        rects=_rects(span),
    )


def _response(output: AnswerOutput) -> AnswerResponse:
    answer = output.answer
    # Rectangles come from the retrieved span, not from the citation, because the
    # citation records *what* was cited and the span records *where* it sits.
    spans_by_label = answer.evidence.by_label()
    return AnswerResponse(
        outcome=answer.outcome.value,
        text=answer.text,
        claims=[
            ClaimResponse(
                text=claim.text,
                citations=[citation.label for citation in claim.citations],
            )
            for claim in answer.claims
        ],
        citations=[
            CitationResponse(
                label=citation.label,
                chunk_id=citation.chunk_id,
                document_id=citation.document_id,
                document_title=citation.document_title,
                page=citation.page,
                char_start=citation.char_start,
                char_end=citation.char_end,
                quoted_text=citation.quoted_text,
                rects=_rects(spans_by_label.get(citation.label)),
            )
            for citation in answer.citations
        ],
        refusal_reason=answer.refusal_reason,
        strategy=answer.evidence.strategy.value,
        retrieval_config_hash=answer.evidence.config_hash,
        model_version=answer.model_version,
        prompt_version=answer.prompt_version,
        embedding_model=output.embedding_model,
        latency_ms=output.latency_ms,
        input_tokens=answer.input_tokens,
        output_tokens=answer.output_tokens,
    )


@router.post(
    "/ask",
    response_model=AnswerResponse,
    summary="Ask a question; every claim carries a resolvable citation",
)
async def ask(request: AskRequest, actor: ActorDep, container: ContainerDep) -> AnswerResponse:
    output = await container.retrieval.ask.ask(actor, _request(request))
    return _response(output)


def _sse(event: str, data: object) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


def _encode(event: AnswerEvent) -> bytes:
    if isinstance(event, EvidenceFound):
        return _sse("evidence", [_passage(s).model_dump(mode="json") for s in event.bundle.spans])
    if isinstance(event, ClaimReady):
        claim = ClaimResponse(
            text=event.claim.text, citations=[c.label for c in event.claim.citations]
        )
        return _sse("claim", claim.model_dump(mode="json"))
    if isinstance(event, Withdrawn):
        return _sse("withdrawn", {"reason": event.reason})
    return _sse("done", _response(event.output).model_dump(mode="json"))


@router.post(
    "/ask/stream",
    summary="Ask a question and receive each validated claim as it is written",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": (
                "Server-sent events: `evidence` (the passages that may be cited), then "
                "`claim` for each claim as it is validated, `withdrawn` if the claims so "
                "far must be discarded because a corrected attempt follows, and finally "
                "`done` with the same body `/ask` returns. `error` if something fails "
                "after the stream has started."
            ),
        }
    },
)
async def ask_stream(
    request: AskRequest, actor: ActorDep, container: ContainerDep
) -> StreamingResponse:
    events = container.retrieval.ask.ask_stream(actor, _request(request))
    # Run up to the first event before answering, so a refusal -- no permission, budget
    # spent, an empty question -- is an ordinary HTTP error, not an event in a 200.
    first = await anext(events)

    async def body() -> AsyncIterator[bytes]:
        yield _encode(first)
        try:
            async for event in events:
                yield _encode(event)
        except DomainError as error:
            yield _sse("error", {"code": error.code, "message": str(error)})
        except Exception as error:
            _logger.exception("answer_stream_failed")
            yield _sse("error", {"code": "internal_error", "message": type(error).__name__})

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        # No caching anywhere, and no buffering in a proxy that would hold every event
        # back until the answer is complete.
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
