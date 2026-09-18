"""A trace survives the outbox: the worker's span is a child of the request's."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from insightlm.shared.observability.telemetry import continued_from, current_trace_carrier

exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
tracer = provider.get_tracer("test")


def test_a_stored_carrier_continues_the_original_trace() -> None:
    exporter.clear()
    with trace.use_span(tracer.start_span("POST /documents"), end_on_exit=True) as request:
        carrier = current_trace_carrier()  # what the outbox stores with the event
    assert "traceparent" in carrier

    # Later, elsewhere: the worker restores it from the event's attributes.
    with continued_from(carrier), tracer.start_as_current_span("ingest_document_version"):
        pass

    spans = {s.name: s for s in exporter.get_finished_spans()}
    worker = spans["ingest_document_version"]
    assert worker.context.trace_id == request.get_span_context().trace_id
    assert worker.parent is not None
    assert worker.parent.span_id == request.get_span_context().span_id


def test_no_carrier_starts_a_fresh_trace_instead_of_failing() -> None:
    exporter.clear()
    with continued_from(None), tracer.start_as_current_span("orphan"):
        pass
    [span] = exporter.get_finished_spans()
    assert span.parent is None
