"""Grounded question answering."""

from __future__ import annotations

from fastapi import APIRouter

from insightlm.api.deps import ActorDep, ContainerDep
from insightlm.api.schemas import (
    AnswerResponse,
    AskRequest,
    CitationResponse,
    ClaimResponse,
)
from insightlm.retrieval.application.answer import AnswerRequest
from insightlm.shared.domain.ids import DocumentId

router = APIRouter(tags=["ask"])


@router.post(
    "/ask",
    response_model=AnswerResponse,
    summary="Ask a question; every claim carries a resolvable citation",
)
async def ask(request: AskRequest, actor: ActorDep, container: ContainerDep) -> AnswerResponse:
    output = await container.retrieval.ask.ask(
        actor,
        AnswerRequest(
            question=request.question,
            top_k=request.top_k,
            document_ids=(
                tuple(DocumentId(d) for d in request.document_ids) if request.document_ids else None
            ),
        ),
    )
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
                rects=[
                    {"page": r.page, "x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1}
                    for r in (
                        spans_by_label[citation.label].span.rects
                        if citation.label in spans_by_label
                        else ()
                    )
                ],
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
