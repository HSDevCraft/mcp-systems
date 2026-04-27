.PHONY: help setup lint format type-check test test-fast test-cov \
        api docker-up docker-down docker-infra clean \
        quickstart health-check validate-module generate-keys

PYTHON  := python
PIP     := pip
UVICORN := uvicorn
SRC     := src
TESTS   := tests

# ── Colours ──────────────────────────────────────────────────────────────────
BOLD  := \033[1m
RESET := \033[0m
GREEN := \033[32m

help: ## Show this help message
	@echo "$(BOLD)MCP System — available targets$(RESET)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-20s$(RESET) %s\n", $$1, $$2}'

# ── Setup ─────────────────────────────────────────────────────────────────────
setup: ## Install all dependencies (prod + dev)
	$(PIP) install -e ".[dev]"
	pre-commit install

setup-prod: ## Install production dependencies only
	$(PIP) install -e .

# ── Code Quality ──────────────────────────────────────────────────────────────
lint: ## Run ruff linter
	ruff check $(SRC) $(TESTS)

format: ## Format code with black + isort
	black $(SRC) $(TESTS)
	isort $(SRC) $(TESTS)

format-check: ## Check formatting without modifying files
	black --check $(SRC) $(TESTS)
	isort --check-only $(SRC) $(TESTS)

type-check: ## Run mypy type checker
	mypy $(SRC)

quality: lint format-check type-check ## Run all quality checks

# ── Testing ───────────────────────────────────────────────────────────────────
test-fast: ## Run unit tests only (fast, no external deps)
	pytest $(TESTS)/unit -m "not slow" -x

test: ## Run full test suite with coverage
	pytest $(TESTS) --cov=$(SRC) --cov-report=term-missing --cov-report=xml

test-cov: ## Generate HTML coverage report
	pytest $(TESTS) --cov=$(SRC) --cov-report=html
	@echo "Coverage report: htmlcov/index.html"

test-integration: ## Run integration tests only
	pytest $(TESTS)/integration -m integration -v

test-watch: ## Run tests in watch mode (requires pytest-watch)
	ptw $(TESTS)/unit -- -x

# ── Running ───────────────────────────────────────────────────────────────────
api: ## Start FastAPI dev server with hot-reload
	$(UVICORN) src.api.main:app --reload --host 0.0.0.0 --port 8000 \
		--log-level info

api-prod: ## Start FastAPI production server
	$(UVICORN) src.api.main:app --host 0.0.0.0 --port 8000 \
		--workers 4 --no-access-log

# ── Docker ────────────────────────────────────────────────────────────────────
docker-infra: ## Start only infrastructure (Redis, Qdrant)
	docker-compose up -d redis qdrant

docker-up: ## Start full docker-compose stack
	docker-compose up --build -d

docker-down: ## Stop and remove containers
	docker-compose down -v

docker-logs: ## Tail all container logs
	docker-compose logs -f

docker-build: ## Build MCP API image
	docker build -t mcp-system:latest -f deploy/Dockerfile .

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean: ## Remove build artifacts, cache, coverage files
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	find . -type f -name "coverage.xml" -delete 2>/dev/null || true

# ── Utilities ────────────────────────────────────────────────────────────────
generate-secret: ## Generate a random secret key
	$(PYTHON) -c "import secrets; print(secrets.token_urlsafe(32))"

generate-fernet: ## Generate a Fernet encryption key
	$(PYTHON) -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

migrate: ## Run database migrations (alembic)
	alembic upgrade head

shell: ## Open IPython shell with app context
	$(PYTHON) -c "from src.utils.config import get_settings; s = get_settings(); print('Settings loaded:', s.mcp_env)"

quickstart: ## Run the in-process quickstart demo (no infra needed)
	$(PYTHON) scripts/quickstart.py

health-check: ## Check health of a running MCP API instance
	$(PYTHON) scripts/health_check.py --url http://localhost:8000

generate-keys: ## Generate MCP_SECRET_KEY, JWT_SECRET_KEY, STORAGE_ENCRYPTION_KEY
	$(PYTHON) scripts/generate_keys.py

validate-module: ## Validate a module file (usage: make validate-module FILE=path/to/module.py)
	$(PYTHON) scripts/register_module.py $(FILE)
