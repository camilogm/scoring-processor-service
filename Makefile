.DEFAULT_GOAL := help
.PHONY: help install db db-stop db-shell run dev test lint migrate migration downgrade db-current db-history \
        up down watch build logs ps restart-check repeatability clean \
        coverage sonar-up sonar-scan sonar-report sonar-open sonar-down sonar-clean \
        fly-setup fly-secrets fly-choices-off password deploy fly-logs fly-open spend

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

# --- Deploy (Fly.io) -----------------------------------------------------------------------
# One long-lived machine running the same Dockerfile; the model goes through Vercel AI Gateway.
# Why not Vercel hosting: docs/adr/0001-deploy-on-fly.md. Needs flyctl: brew install flyctl.

FLY        ?= fly
FLY_APP    ?= clip-scoring
FLY_REGION ?= dfw
# A value from the environment or .env (shell snippet), never echoed or passed as an argument.
env_value = $${$(1):-$$(awk -v k=$(1) 'index($$0, k "=") == 1 {v = substr($$0, length(k) + 2)} END {print v}' .env 2>/dev/null)}
GATEWAY_KEY = $(call env_value,AI_GATEWAY_API_KEY)

fly-setup: ## One-time: create the Fly app, its volume and Postgres, and push the secrets
	@command -v $(FLY) >/dev/null || (echo "flyctl not found: brew install flyctl && fly auth login" && exit 1)
	$(FLY) apps create $(FLY_APP)
	$(FLY) volumes create clip_data -a $(FLY_APP) -r $(FLY_REGION) --size 10 --yes
	$(FLY) mpg create -n $(FLY_APP)-db -r $(FLY_REGION)
	@$(MAKE) --no-print-directory fly-secrets
	@echo "Last step: fly mpg list, then fly mpg attach <cluster-id> -a $(FLY_APP) (sets DATABASE_URL)"

fly-secrets: ## Push the gateway key, the Basic auth credentials and LLM_MODEL_CHOICES if set (environment or .env) to Fly
	@key="$(GATEWAY_KEY)"; user="$(call env_value,BASIC_AUTH_USER)"; password="$(call env_value,BASIC_AUTH_PASSWORD)"; \
		choices="$(call env_value,LLM_MODEL_CHOICES)"; \
		test -n "$$key" || { echo "AI_GATEWAY_API_KEY is not set in the environment or .env"; exit 1; }; \
		test -n "$$user" -a -n "$$password" || { echo "BASIC_AUTH_USER and BASIC_AUTH_PASSWORD are required: the deployment is public (make password)"; exit 1; }; \
		test -z "$$choices" || echo "Model choice ON for $(FLY_APP): $$choices (turn off: make fly-choices-off)"; \
		{ printf 'AI_GATEWAY_API_KEY=%s\nBASIC_AUTH_USER=%s\nBASIC_AUTH_PASSWORD=%s\n' "$$key" "$$user" "$$password"; \
		  test -z "$$choices" || printf 'LLM_MODEL_CHOICES=%s\n' "$$choices"; } \
		| $(FLY) secrets import -a $(FLY_APP)

fly-choices-off: ## Turn per-upload model choice off on Fly (removes LLM_MODEL_CHOICES)
	$(FLY) secrets unset LLM_MODEL_CHOICES -a $(FLY_APP)

password: ## Print a random BASIC_AUTH_PASSWORD line to paste into .env
	@printf 'BASIC_AUTH_PASSWORD=%s\n' "$$(openssl rand -base64 24 | tr -d '/+=')"

deploy: ## Build and deploy to Fly.io (one machine: the worker and the sweep assume a single instance)
	$(FLY) deploy -a $(FLY_APP) --ha=false

fly-logs: ## Follow the deployed app's logs
	$(FLY) logs -a $(FLY_APP)

fly-open: ## Open the deployed demo page
	$(FLY) open /demo -a $(FLY_APP)

spend: ## Vercel AI Gateway balance and total spend in USD (per clip: provenance.cost_usd)
	@key="$(GATEWAY_KEY)"; test -n "$$key" || (echo "AI_GATEWAY_API_KEY is not set in the environment or .env" && exit 1); \
		printf 'Authorization: Bearer %s' "$$key" | curl -fsS -H @- https://ai-gateway.vercel.sh/v1/credits; echo

# --- Quality (SonarQube) -------------------------------------------------------------------
# A real SonarQube, run locally, so the quality numbers quoted anywhere in this repo can be
# reproduced instead of trusted. Separate compose project from the app stack on purpose (see
# quality/docker-compose.yml): `make down` never touches the analysis history.

QUALITY       := quality
SONAR_URL     ?= http://localhost:9002
SONAR_NET     := scoring-quality_default
SONAR_TOKEN    = $(shell cat $(QUALITY)/.sonar-token 2>/dev/null)
SONAR_COMPOSE := $(COMPOSE) -f $(QUALITY)/docker-compose.yml

coverage: db ## Run the tests with coverage (coverage.xml for Sonar, summary in the terminal)
	uv run pytest --cov --cov-report=xml --cov-report=term $(T)

sonar-up: ## Start the local SonarQube and provision an analysis token
	SONAR_URL=$(SONAR_URL) ./$(QUALITY)/sonar-up.sh

sonar-scan: coverage ## Analyse the code with coverage and print the report
	@test -n "$(SONAR_TOKEN)" || (echo "no analysis token — run: make sonar-up" && exit 1)
	@# Runs on SonarQube's own compose network and addresses it by service name: a container
	@# cannot reach the host's published port on macOS. coverage.xml carries repo-relative
	@# paths (relative_files in pyproject.toml), which is what makes it match under /usr/src.
	docker run --rm \
		--network $(SONAR_NET) \
		-e SONAR_HOST_URL="http://sonarqube:9000" \
		-e SONAR_TOKEN="$(SONAR_TOKEN)" \
		-v "$(CURDIR):/usr/src" \
		sonarsource/sonar-scanner-cli \
		-Dsonar.projectKey=scoring-processor-service \
		-Dsonar.projectName="Scoring Processor Service" \
		-Dsonar.sources=app,scripts \
		-Dsonar.tests=tests \
		-Dsonar.exclusions="**/__pycache__/**" \
		-Dsonar.python.version=3.12 \
		-Dsonar.python.coverage.reportPaths=coverage.xml \
		-Dsonar.sourceEncoding=UTF-8
	@$(MAKE) --no-print-directory sonar-report

sonar-report: ## Print the SonarQube quality report
	@SONAR_URL=$(SONAR_URL) ./$(QUALITY)/sonar-report.py

sonar-open: ## Open the SonarQube dashboard
	@open $(SONAR_URL)/dashboard?id=scoring-processor-service 2>/dev/null || echo "$(SONAR_URL)"

sonar-down: ## Stop SonarQube, keeping its analysis history
	$(SONAR_COMPOSE) down

sonar-clean: ## Stop SonarQube and delete its volumes and token
	$(SONAR_COMPOSE) down --volumes
	@rm -f $(QUALITY)/.sonar-token

clean: ## Remove caches
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml .scannerwork
	find app tests scripts -type d -name __pycache__ -prune -exec rm -rf {} +
