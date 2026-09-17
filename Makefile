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

test-fast: ## Unit and architecture tests only (no Docker needed)
	uv run pytest tests/unit tests/architecture -q

check: lint types arch ## Everything CI checks, locally

lint: ## ruff
	uv run ruff check src tests migrations spikes
	uv run ruff format --check src tests migrations spikes

format: ## Apply formatting
	uv run ruff check src tests migrations spikes --fix
	uv run ruff format src tests migrations spikes

types: ## mypy --strict
	uv run mypy

arch: ## Verify the architecture contracts
	uv run lint-imports

.PHONY: help install up down logs migrate revision dev worker test test-fast check lint types format arch
