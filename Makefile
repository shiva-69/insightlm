.DEFAULT_GOAL := help
SHELL := /bin/bash

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies
	uv sync --all-groups

up: ## Start the whole stack
	docker compose up -d --build

down: ## Stop the stack
	docker compose down

logs: ## Tail application logs
	docker compose logs -f api worker

migrate: ## Apply migrations
	uv run alembic upgrade head

revision: ## Autogenerate a migration: make revision m="add documents"
	uv run alembic revision --autogenerate -m "$(m)"

dev: ## Run the API with reload
	uv run uvicorn insightlm.api.app:create_app --factory --reload

worker: ## Run the ARQ worker
	uv run arq insightlm.worker.main.WorkerSettings

test: ## Run every test
	uv run pytest

eval: ## Run the golden set against every configuration
	uv run insightlm-eval

eval-gate: ## Run the CI regression gate locally (offline adapters, deterministic)
	GENERATION_PROVIDER=offline GEMINI_API_KEY= VOYAGE_API_KEY= ANTHROPIC_API_KEY= \
		uv run insightlm-eval --config D-hybrid --corpus generated --count 40 --min-ndcg 0.56

eval-judge: ## Criterion judge benchmark (offline free; add JUDGE=gemini for the model, cached)
	uv run insightlm-eval-judge $(if $(JUDGE),--judge $(JUDGE))

eval-injection: ## Injection red-team: detection, false positives, effect on scores (free)
	uv run insightlm-eval-injection --assess

eval-fairness: ## Counterfactual fairness (offline free; JUDGE=gemini for the model, cached)
	uv run insightlm-eval-fairness $(if $(JUDGE),--judge $(JUDGE))

fuzz: ## Schemathesis against a running API (start it keyless with RATE_LIMITS_ENABLED=false)
	@TOKEN=$$(uv run python scripts/fuzz_token.py) && \
		uv run schemathesis run http://localhost:8000/openapi.json -H "Authorization: Bearer $$TOKEN" \
		--checks not_a_server_error,response_schema_conformance --max-examples 60 --workers 4

load: ## 50-user k6 load test against the SLOs (run the stack keyless with RATE_LIMITS_ENABLED=false)
	uv run python scripts/seed_demo.py --cohort 30 --out eval/.cache/demo.json > /dev/null
	docker run --rm --network insightlm_default -v $(PWD)/infra/k6:/scripts:ro \
		-v $(PWD)/eval/.cache/demo.json:/data/demo.json:ro -v $(PWD)/eval/.cache:/out \
		grafana/k6:0.54.0 run --summary-export /out/k6-summary.json /scripts/load.js

a11y: ## axe-core (WCAG 2.1 AA) on every page + phone-width overflow check (needs `make load` seed)
	mkdir -p eval/.cache/a11y && cd web && node scripts/a11y.mjs ../eval/.cache/a11y ../eval/.cache/demo.json

diagrams: ## Regenerate the ER diagram from the models and render every diagram to SVG
	uv run python scripts/er_diagram.py > docs/diagrams/er.mmd
	for f in context containers components er; do \
		docker run --rm -u "$$(id -u):$$(id -g)" -v $(PWD)/docs/diagrams:/data minlag/mermaid-cli:11.4.0 \
			-i /data/$$f.mmd -o /data/svg/$$f.svg -b transparent; done

samples: ## Write sample CVs (strong, weak, paraphrase, attacked) into ./samples
	uv run insightlm-samples --cohort 50

coverage: ## Whole suite with coverage; fails under 85% (as CI does)
	uv run pytest -q --cov --cov-report=term-missing --cov-fail-under=85

test-fast: ## Unit and architecture tests only (no Docker needed)
	uv run pytest tests/unit tests/architecture -q

check: lint types arch ## Everything CI checks, locally

lint: ## ruff
	uv run ruff check src tests migrations spikes scripts
	uv run ruff format --check src tests migrations spikes scripts

format: ## Apply formatting
	uv run ruff check src tests migrations spikes scripts --fix
	uv run ruff format src tests migrations spikes scripts

types: ## mypy --strict
	uv run mypy

arch: ## Verify the architecture contracts
	uv run lint-imports

.PHONY: help install up down logs migrate revision dev worker test eval eval-gate eval-judge eval-injection eval-fairness samples test-fast check lint format types arch fuzz load a11y coverage diagrams
