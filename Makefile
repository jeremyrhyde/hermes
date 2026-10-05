# Hermes
#
# Common workflows wrapped as `make` targets. Run `make help` for the list.
# Most targets shell out to `uv` — install it first: https://docs.astral.sh/uv/

# Resolve `uv`: prefer one already on PATH, else the location the official
# installer drops it (~/.local/bin). Override with `make UV=/path/to/uv ...`.
UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
PYTHON := $(UV) run python
PYTEST := $(UV) run pytest

# Host/port for the dev server. The plain `make run` target reads these from
# config.Settings (i.e. the environment / .env) instead, so they only apply
# to run-dev.
HOST ?= 0.0.0.0
PORT ?= 8002

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

.PHONY: help
help:
	@echo "Hermes — make targets"
	@echo ""
	@echo "Pipeline (Linux + macOS):"
	@echo "  make setup          Ensure the uv toolchain is installed"
	@echo "  make build          Sync deps into .venv and byte-compile sources"
	@echo "  make run            Start the server in the foreground"
	@echo "  -> full bootstrap:  make setup build run"
	@echo ""
	@echo "Setup:"
	@echo "  make install        Alias for build"
	@echo "  make lock           Re-lock dependencies (regenerate uv.lock)"
	@echo "  make clean          Remove caches, build artefacts, *.pyc"
	@echo "  make distclean      clean + remove .venv and uv.lock"
	@echo ""
	@echo "Run:"
	@echo "  make run            Start the FastAPI server (host/port from .env)"
	@echo "  make run-dev        Start with auto-reload (HOST/PORT overridable)"
	@echo "  make open           Open the web UI in a browser"
	@echo ""
	@echo "Tests:"
	@echo "  make test           Run the pytest suite"
	@echo "  make health         curl /health"
	@echo "  make sources        list configured sources and their health"
	@echo "  make feed           print the 10 most recent feed items"
	@echo "  make poll-now ID=x  force an immediate poll of one source"
	@echo ""
	@echo "Examples:"
	@echo "  make run-dev PORT=9000"
	@echo "  make poll-now ID=astralcodexten"

# ---------------------------------------------------------------------------
# Setup / build
# ---------------------------------------------------------------------------

# setup — ensure the uv toolchain exists. Idempotent; uses the official
# installer only when uv is missing, preferring curl and falling back to wget.
.PHONY: setup
setup:
	@if [ -x "$(UV)" ] || command -v uv >/dev/null 2>&1; then \
		echo "uv already present: $$($(UV) --version 2>/dev/null || echo $(UV))"; \
	else \
		echo "Installing uv (Linux/macOS)..."; \
		if command -v curl >/dev/null 2>&1; then \
			curl -LsSf https://astral.sh/uv/install.sh | sh; \
		elif command -v wget >/dev/null 2>&1; then \
			wget -qO- https://astral.sh/uv/install.sh | sh; \
		else \
			echo "ERROR: need curl or wget to install uv. See https://docs.astral.sh/uv/"; \
			exit 1; \
		fi; \
		echo "uv installed to $(HOME)/.local/bin — ensure it is on your PATH."; \
	fi

# build — sync locked deps into .venv, then byte-compile the sources so a
# syntax error fails the build on any platform.
.PHONY: build
build:
	$(UV) sync
	$(UV) run python -m compileall -q core services schemas main.py config.py
	@echo "Build complete."

.PHONY: install
install: build

.PHONY: lock
lock:
	$(UV) lock

.PHONY: clean
clean:
	@find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name '*.egg-info' -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete 2>/dev/null || true
	@echo "Cleaned caches and build artefacts."

.PHONY: distclean
distclean: clean
	@rm -rf .venv
	@rm -f uv.lock
	@echo "Removed .venv and uv.lock. Run 'make install' to rebuild."

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

.PHONY: run
run:
	$(PYTHON) main.py

.PHONY: run-dev
run-dev:
	$(UV) run uvicorn main:app --reload --host $(HOST) --port $(PORT)

.PHONY: open
open:
	@python3 -c "import webbrowser; webbrowser.open('http://localhost:$(PORT)/ui/')"

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

.PHONY: test
test:
	$(PYTEST) -v

# ---------------------------------------------------------------------------
# Live checks — the server must already be running.
#
# Override the API host with HERMES_HOST (defaults to http://localhost:8002):
#   make health HERMES_HOST=http://192.168.1.50:8002
# ---------------------------------------------------------------------------

HERMES_HOST ?= http://localhost:8002
ID ?=

.PHONY: health
health:
	@curl -sS $(HERMES_HOST)/health && echo ""

.PHONY: feed
feed:
	@curl -sS "$(HERMES_HOST)/api/feed/?limit=10" | python3 -m json.tool

.PHONY: sources
sources:
	@curl -sS $(HERMES_HOST)/api/sources/ | python3 -m json.tool

.PHONY: poll-now
poll-now:
	@if [ -z "$(ID)" ]; then \
		echo "ERROR: pass ID=<source-id>"; \
		echo "  List them with: make sources"; \
		exit 1; \
	fi
	@curl -sS -X POST $(HERMES_HOST)/api/sources/$(ID)/poll && echo ""
