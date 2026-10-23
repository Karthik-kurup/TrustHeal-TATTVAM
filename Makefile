.PHONY: install dev train test demo typecheck lint clean help

# ── Colours ─────────────────────────────────────────────────────────────────
CYAN  := \033[0;36m
RESET := \033[0m

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-14s$(RESET) %s\n", $$1, $$2}'

# ── Install ──────────────────────────────────────────────────────────────────
install: ## Install all dependencies (Python + Node)
	cd backend && pip install -e ".[dev]"
	cd frontend && npm install
	@echo "$(CYAN)Install complete.$(RESET)"

# ── Dev server ───────────────────────────────────────────────────────────────
dev: ## Start backend (Uvicorn) and frontend (Vite) concurrently
	@echo "$(CYAN)Starting TrustHeal dev servers…$(RESET)"
	@trap 'kill 0' EXIT; \
	  cd backend && uvicorn app.main:app --reload --port 8000 --log-level info & \
	  cd frontend && npm run dev & \
	  wait

# ── Tests ────────────────────────────────────────────────────────────────────
test: ## Run backend pytest suite
	cd backend && python -m pytest tests/ -v --tb=short

test-cov: ## Run tests with coverage report
	cd backend && python -m pytest tests/ -v --cov=app --cov-report=term-missing

# ── Training ─────────────────────────────────────────────────────────────────
train: ## Generate dataset and train all models (headless, target < 90 s)
	@echo "$(CYAN)Generating training dataset…$(RESET)"
	cd backend && python -m training.generate_dataset
	@echo "$(CYAN)Generating separate unseen-fault evaluation dataset…$(RESET)"
	cd backend && python -m training.generate_dataset --unseen
	@echo "$(CYAN)Training models…$(RESET)"
	cd backend && python -m training.train
	@echo "$(CYAN)Training complete. Report: models/report.json$(RESET)"

# ── TypeScript type generation ───────────────────────────────────────────────
typecheck: ## Export OpenAPI schema and generate TypeScript types
	@echo "$(CYAN)Exporting OpenAPI schema…$(RESET)"
	cd backend && python -c "import json; from app.main import app; print(json.dumps(app.openapi()))" > openapi.json
	@echo "$(CYAN)Generating TypeScript types…$(RESET)"
	cd frontend && npm run generate-api
	@echo "$(CYAN)TypeScript typecheck…$(RESET)"
	cd frontend && npm run typecheck
	@echo "$(CYAN)Done.$(RESET)"

# ── Demo mode ────────────────────────────────────────────────────────────────
demo: ## Start in presenter/demo mode with guided demo pre-loaded
	@echo "$(CYAN)Starting TrustHeal in demo mode…$(RESET)"
	@trap 'kill 0' EXIT; \
	  TRUSTHEAL_SPEED=10 cd backend && uvicorn app.main:app --port 8000 --log-level warning & \
	  cd frontend && VITE_DEMO_MODE=true npm run dev & \
	  wait

# ── Lint ─────────────────────────────────────────────────────────────────────
lint: ## Lint Python (ruff) and TypeScript (eslint)
	cd backend && python -m ruff check app/ twin/ adapters/ telemetry/ detection/ diagnosis/ trust/ healing/ triage/ store/ training/
	cd frontend && npm run lint

# ── Clean ────────────────────────────────────────────────────────────────────
clean: ## Remove generated files and caches
	find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find backend -name "*.pyc" -delete 2>/dev/null || true
	rm -f backend/openapi.json
	rm -rf frontend/src/api/generated
	rm -rf frontend/node_modules/.vite
	@echo "$(CYAN)Clean complete.$(RESET)"
