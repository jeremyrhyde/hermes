# Hermes
#
# Common workflows wrapped as `make` targets. Run `make help` for the list.
# Most targets shell out to `uv` — install it first: https://docs.astral.sh/uv/
# The UI needs Node 20+ to build (`make setup` installs it if missing).

# Resolve `uv`: prefer one already on PATH, else the location the official
# installer drops it (~/.local/bin). Override with `make UV=/path/to/uv ...`.
UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
PYTHON := $(UV) run python
PYTEST := $(UV) run pytest
NPM ?= npm
FRONTEND := frontend

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
	@echo "  make setup          Ensure uv and Node 20+ are installed"
	@echo "  make build          Sync deps, byte-compile, build the UI into frontend/dist"
	@echo "  make run            Start the server in the foreground"
	@echo "  -> full bootstrap:  make setup build run"
	@echo ""
	@echo "Setup:"
	@echo "  make install        Alias for build"
	@echo "  make lock           Re-lock dependencies (regenerate uv.lock)"
	@echo "  make clean          Remove caches, build artefacts, frontend/dist"
	@echo "  make distclean      clean + remove .venv, uv.lock and frontend/node_modules"
	@echo ""
	@echo "Run:"
	@echo "  make run            Start the FastAPI server (host/port from .env)"
	@echo "  make run-dev        API with reload on :$(PORT) + UI dev server on :5173"
	@echo "  make open           Open the web UI in a browser"
	@echo ""
	@echo "Tests:"
	@echo "  make test           pytest, then vitest and type checks"
	@echo "  make check          svelte-check + tsc on the UI"
	@echo "  make health         curl /health"
	@echo "  make sources        list configured sources and their health"
	@echo "  make feed           print the 10 most recent feed items"
	@echo "  make poll-now ID=x  force an immediate poll of one source"
	@echo ""
	@echo "Background service (systemd on Linux/Pi, launchd on macOS):"
	@echo "  make service-install    Install + start at boot/login"
	@echo "  make service-uninstall  Stop and remove it"
	@echo "  make service-status     Show whether it is running"
	@echo "  make service-logs       Follow its logs"
	@echo "  make service-restart    Restart it (e.g. after a git pull)"
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
	@if command -v node >/dev/null 2>&1 && [ "$$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null)" -ge 20 ] 2>/dev/null; then \
		echo "node present: $$(node --version)"; \
	else \
		echo "Node.js 20+ not found — installing (needed to build the UI)..."; \
		if [ "$$(uname)" = "Darwin" ]; then \
			command -v brew >/dev/null 2>&1 || { echo "ERROR: Homebrew required. Install from https://brew.sh, then re-run 'make setup'."; exit 1; }; \
			brew install node; \
		elif command -v apt-get >/dev/null 2>&1; then \
			command -v curl >/dev/null 2>&1 || { echo "ERROR: need curl to install Node. Install curl, then re-run 'make setup'."; exit 1; }; \
			SUDO=""; [ "$$(id -u)" -ne 0 ] && SUDO="sudo"; \
			if [ -n "$$SUDO" ] && ! sudo -n true 2>/dev/null; then echo "  (installing Node system-wide — you may be prompted for your sudo password)"; fi; \
			curl -fsSL https://deb.nodesource.com/setup_20.x -o /tmp/nodesource_setup.sh && \
			$$SUDO bash /tmp/nodesource_setup.sh && \
			$$SUDO apt-get install -y nodejs && \
			rm -f /tmp/nodesource_setup.sh; \
		else \
			echo "ERROR: cannot auto-install Node 20+ on this OS."; \
			echo "  Install Node 20+ from https://nodejs.org/en/download, then re-run 'make setup'."; \
			exit 1; \
		fi; \
		if command -v node >/dev/null 2>&1 && [ "$$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null)" -ge 20 ] 2>/dev/null; then \
			echo "node installed: $$(node --version)"; \
		else \
			echo "ERROR: Node install did not produce Node 20+ on PATH. See https://nodejs.org/en/download"; \
			exit 1; \
		fi; \
	fi

# build — sync locked deps into .venv, byte-compile the sources so a syntax
# error fails the build, then build the UI into frontend/dist.
.PHONY: build
build:
	$(UV) sync
	$(UV) run python -m compileall -q core services schemas main.py config.py
	cd $(FRONTEND) && $(NPM) ci && $(NPM) run build
	@echo "Build complete."

# check — type-check the UI (svelte-check + tsc). Kept out of build so a Pi
# install stays fast; `make test` runs it.
.PHONY: check
check:
	cd $(FRONTEND) && $(NPM) run check

.PHONY: install
install: build

.PHONY: lock
lock:
	$(UV) lock

.PHONY: clean
clean:
	@find . -path ./$(FRONTEND)/node_modules -prune -o -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name '*.egg-info' -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -path ./$(FRONTEND)/node_modules -prune -o -type f -name '*.pyc' -delete 2>/dev/null || true
	@rm -rf $(FRONTEND)/dist
	@echo "Cleaned caches and build artefacts."

.PHONY: distclean
distclean: clean
	@rm -rf .venv $(FRONTEND)/node_modules
	@rm -f uv.lock
	@echo "Removed .venv, uv.lock and node_modules. Run 'make build' to rebuild."

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

.PHONY: run
run:
	$(PYTHON) main.py

.PHONY: run-dev
run-dev:
	@echo "API on :$(PORT) — UI dev server on http://localhost:5173/"
	@trap 'kill 0' INT TERM EXIT; \
	(cd $(FRONTEND) && HERMES_PORT=$(PORT) $(NPM) run dev -- --host) & \
	$(UV) run uvicorn main:app --reload --host $(HOST) --port $(PORT)

.PHONY: open
open:
	@python3 -c "import webbrowser; webbrowser.open('http://localhost:$(PORT)/')"

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

.PHONY: test
test:
	$(PYTEST) -v
	@if [ -d $(FRONTEND)/node_modules ]; then \
		cd $(FRONTEND) && $(NPM) test && $(NPM) run check; \
	else \
		echo "Skipping vitest and type checks: $(FRONTEND)/node_modules not found. Run 'make build' first."; \
	fi

# ---------------------------------------------------------------------------
# Live checks — the server must already be running.
#
# Override the API host with HERMES_HOST (defaults to http://localhost:8002):
#   make health HERMES_HOST=http://192.168.1.50:8002
# ---------------------------------------------------------------------------

HERMES_HOST ?= http://localhost:$(PORT)
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

# ---------------------------------------------------------------------------
# Background service — see scripts/install-server.sh. Pantheon's installer
# runs `make -C modules/hermes service-install`.
# ---------------------------------------------------------------------------

.PHONY: service-install service-uninstall service-status service-logs service-restart
service-install:
	./scripts/install-server.sh

service-uninstall:
	./scripts/install-server.sh --uninstall

service-status:
	./scripts/install-server.sh --status

service-logs:
	./scripts/install-server.sh --logs

service-restart:
	./scripts/install-server.sh --restart
