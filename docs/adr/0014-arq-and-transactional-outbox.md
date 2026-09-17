# ADR-0014: ARQ on Redis, with a transactional outbox in Postgres

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Ingestion, assessment and scheduled verification run outside the request path. They
need durable dispatch: an event must not be lost because a broker was briefly down, and
must not be published for a transaction that later rolled back.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Celery + RabbitMQ | Battle-tested, mature | Celery's threading model fights asyncio; RabbitMQ is a whole broker and another container for one queue |
| Kafka | Replayable log | Vastly oversized; operationally heavy for a solo project |
| **ARQ on Redis + outbox in Postgres** | Async-native; reuses Redis already present; small enough to read the source of | Weaker delivery guarantees than a real broker — which the outbox makes irrelevant |

## Decision

ARQ for work distribution. Domain events are written to an `outbox_events` table in the
**same transaction** as the domain change, and a relay drains them onto the queue.

## Rationale

The pairing is the point. The classic failure — commit the row, then fail to publish the
event, or publish and then roll back — is solved by the outbox, not by the broker. Once
durability lives in Postgres, the queue is only a work distributor, and ARQ's weaker
guarantees stop mattering. That in turn lets us delete RabbitMQ from the topology.

The relay polls on a schedule rather than being called by writers: a relay that must be
invoked is a relay that will eventually be forgotten.

## Consequences

**Easy:** three infrastructure containers instead of five; events are durable with their
writes; redelivery is observable via `attempts` and `last_error`.

**Hard:** at-least-once delivery, so consumers must be idempotent. Polling adds latency
between commit and publish (bounded by the cron interval, currently 10s).

**We now owe:** idempotency keys on every consumer, and a dead-letter view before
Phase 1 ingestion depends on this.

## Reversal

Trigger: needing replayable event history (for example, rebuilding a read model from
scratch) or fan-out to multiple independent consumer groups. The outbox table *is* the
log, so migration means pointing the relay at Kafka and leaving every writer unchanged.
Estimated cost: two days.
