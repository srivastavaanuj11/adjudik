.DEFAULT_GOAL := help
SHELL := /bin/bash

# Run a one-off command in the server container with the owner DB URL.
COMPOSE := docker compose

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

.PHONY: setup
setup: ## uv sync, npm ci, create .env from example if missing
	uv sync --locked
	cd web && npm ci
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")

.PHONY: dev
dev: ## Start the full development stack
	$(COMPOSE) up

.PHONY: down
down: ## Stop the development stack
	$(COMPOSE) down

.PHONY: seed
seed: ## Load demo data (SPEC §16.5)
	$(COMPOSE) exec server python manage.py seed_demo

.PHONY: test
test: ## Core, server and web unit tests
	uv run pytest
	cd web && npm test

.PHONY: e2e-seed
e2e-seed: ## Seed demo data with a fixed password for Playwright (SPEC §16.5)
	$(COMPOSE) exec -e ADJUDIK_SEED_PASSWORD=$(E2E_PASSWORD) server \
		python manage.py seed_demo --password $(E2E_PASSWORD)

.PHONY: e2e
e2e: ## Playwright against the compose stack (run `make e2e-seed` first)
	cd web && E2E_PASSWORD=$(E2E_PASSWORD) npm run e2e

# Fixed password shared by `e2e-seed` and the Playwright flows.
E2E_PASSWORD ?= adjudik-e2e-pw

.PHONY: lint
lint: ## ruff, mypy, import-linter, ESLint, tsc, pip-audit, npm audit
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy packages/core/src
	uv run lint-imports
	uv run pip-audit || true
	cd web && npm run lint && npx tsc --noEmit && (npm audit --audit-level=high || true)

.PHONY: fmt
fmt: ## Auto-format Python and web
	uv run ruff format .
	uv run ruff check --fix .
	cd web && npm run format

.PHONY: openapi
openapi: ## Regenerate openapi.json and the web client
	$(COMPOSE) exec server python manage.py spectacular --format openapi-json --file /app/schemas/openapi.json
