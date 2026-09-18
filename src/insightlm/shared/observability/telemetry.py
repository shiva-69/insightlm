"""Traces and metrics, exported over OTLP to the collector.

One trace follows a document from the upload request, through the transactional outbox
and the queue, into the worker's ingestion steps and out to the model provider. The
outbox is where naive tracing breaks: the request that wrote the event has finished
before the worker picks it up. So the W3C ``traceparent`` is stored with the event and
restored by the worker, and the worker's spans join the request's trace instead of
starting an unrelated one.

Off unless ``OTEL_ENABLED``: tests and local scripts produce no telemetry and need no
collector. The instruments below are created against the global (proxy) providers, so
they're safe to use whether or not telemetry was switched on.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import context, metrics, propagate, trace

_tenant: contextvars.ContextVar[str | None] = contextvars.ContextVar("tenant", default=None)

tracer = trace.get_tracer("insightlm")
meter = metrics.get_meter("insightlm")

MODEL_CALLS = meter.create_counter(
    "insightlm.model.calls", description="Paid model calls, by kind, model and workspace"
)
MODEL_TOKENS = meter.create_counter("insightlm.model.tokens", unit="{token}")
MODEL_COST = meter.create_counter("insightlm.model.cost", unit="USD")
MODEL_LATENCY = meter.create_histogram("insightlm.model.duration", unit="ms")
INGESTION_STEP = meter.create_histogram("insightlm.ingestion.step.duration", unit="ms")
DEAD_LETTERS = meter.create_counter("insightlm.ingestion.dead_letters")

_configured = False


def configure_telemetry(*, enabled: bool, endpoint: str, service: str, environment: str) -> None:
    global _configured
    if not enabled or _configured:
        return
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": service, "deployment.environment": environment})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)
    metrics.set_meter_provider(
        MeterProvider(
            resource=resource,
            metric_readers=[
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=endpoint, insecure=True),
                    export_interval_millis=10_000,
                )
            ],
        )
    )
    _configured = True


def instrument_app(app: Any) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")


def instrument_engine(engine: Any) -> None:
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)


# -- the workspace a unit of work belongs to, for per-tenant metrics ---------------------


def bind_tenant(tenant_id: str | None) -> None:
    _tenant.set(tenant_id)


def current_tenant() -> str:
    return _tenant.get() or "none"


# -- carrying a trace across the queue -----------------------------------------------------


def current_trace_carrier() -> dict[str, str]:
    """The active trace context as W3C headers, to store alongside a queued event."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


@contextmanager
def continued_from(carrier: Mapping[str, str] | None) -> Iterator[None]:
    """Make spans started inside this block children of the stored context."""
    token = context.attach(propagate.extract(dict(carrier or {})))
    try:
        yield
    finally:
        context.detach(token)
