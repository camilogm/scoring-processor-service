.DEFAULT_GOAL := help
.PHONY: help install db db-stop db-shell run dev test lint migrate migration downgrade db-current db-history \
        up down watch build logs ps restart-check repeatability clean

PORT ?= 9500
RUNS ?= 5
COMPOSE := docker compose
ALEMBIC := uv run alembic

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# --- Local ---------------------------------------------------------------------------------

install: ## Install dependencies (uv sync)
	uv sync

db: ## Start only Postgres (needed by the API and the tests)
	$(COMPOSE) up -d --wait db

db-stop: ## Stop Postgres (data stays in the volume)
	$(COMPOSE) stop db

db-shell: ## psql into the database
	$(COMPOSE) exec db psql -U clip -d clip_scoring

run: db ## Run the API locally (needs ffmpeg and an LLM endpoint)
	uv run uvicorn app.main:app --port $(PORT)

dev: db ## Run the API locally with auto-reload on code changes
	uv run uvicorn app.main:app --port $(PORT) --reload --reload-dir app

test: db ## Run the test suite; narrow it with T=tests/test_store.py::test_name
	uv run pytest $(T)

lint: ## Lint with ruff
	uvx ruff check app tests scripts

# --- Migrations (the app also applies pending ones on startup) -----------------------------

migrate: db ## Apply pending migrations
	$(ALEMBIC) upgrade head

migration: ## Create a migration: make migration m="add lease columns"
	@test -n "$(m)" || (echo 'usage: make migration m="what it does"' && exit 1)
	$(ALEMBIC) revision -m "$(m)"

downgrade: db ## Revert migrations, one by default: make downgrade to=0001
	$(ALEMBIC) downgrade $(or $(to),-1)

db-current: db ## Show the revision the database is at
	$(ALEMBIC) current

db-history: ## List migrations
	$(ALEMBIC) history --verbose

# --- Docker --------------------------------------------------------------------------------

up: ## Build and start the full stack in the background
	$(COMPOSE) up --build -d

down: ## Stop the stack (data stays in the volume)
	$(COMPOSE) down

watch: ## Full stack in watch mode: syncs + restarts on code changes, rebuilds on dependency changes
	$(COMPOSE) up --build --watch

build: ## Rebuild the api image
	$(COMPOSE) build api

logs: ## Follow api logs
	$(COMPOSE) logs -f api

ps: ## Show stack status
	$(COMPOSE) ps

# --- Checks against a running server -------------------------------------------------------

restart-check: ## kill -9 durability check: make restart-check CLIP=path/to/clip.mp4
	@test -n "$(CLIP)" || (echo 'usage: make restart-check CLIP=path/to/clip.mp4' && exit 1)
	scripts/restart_check.sh $(CLIP)

repeatability: ## Score stability: make repeatability CLIP=path/to/clip.mp4 RUNS=5
	@test -n "$(CLIP)" || (echo 'usage: make repeatability CLIP=path/to/clip.mp4' && exit 1)
	uv run python scripts/repeatability.py $(CLIP) --runs $(RUNS)

clean: ## Remove caches
	rm -rf .pytest_cache .ruff_cache
	find app tests scripts -type d -name __pycache__ -prune -exec rm -rf {} +
