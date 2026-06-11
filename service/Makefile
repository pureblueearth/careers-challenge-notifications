# Convenience commands for the notification service. Run from service/.
# Override vars inline, e.g.  make workers WORKERS=8   |   make seed USERS=200

PY      := ./.venv/bin/python
UVICORN := ./.venv/bin/uvicorn
# Put Homebrew's keg-only psql on PATH (macOS, arm64 + intel); harmless elsewhere,
# where psql is already on PATH (e.g. Linux apt installs).
export PATH := /opt/homebrew/opt/postgresql@16/bin:/usr/local/opt/postgresql@16/bin:$(PATH)

TARGET  ?= http://localhost:8080
WORKERS ?= 4
USERS   ?= 50
EVENTS  ?= 0

.PHONY: help install db-create db-reset api worker workers seed requeue test \
        gateway eval-smoke eval-baseline eval-burst eval-adversarial metrics ports stop

help: ## show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## create the venv and install deps (runtime + dev) via uv
	uv venv && uv pip install -r requirements.txt -r requirements-dev.txt

db-create: ## create the dev and test databases
	createdb notifications 2>/dev/null || true
	createdb notifications_test 2>/dev/null || true

db-reset: ## drop + recreate the dev database (schema re-applied on next boot)
	dropdb --if-exists notifications && createdb notifications

api: ## run the intake API on :8080
	$(UVICORN) api:app --host 0.0.0.0 --port 8080

worker: ## run a single delivery worker
	$(PY) worker.py

workers: ## run N delivery workers (WORKERS=4)
	bash scripts/run_workers.sh $(WORKERS)

seed: ## seed the DB directly: devices + optional events (USERS=50 EVENTS=0; no services needed)
	$(PY) scripts/seed_database.py --users $(USERS) --events $(EVENTS)

test: ## run the test suite
	$(PY) -m pytest

gateway: ## run the mock push gateway on :9000
	python3 ../mock_gateway/server.py

eval-smoke: ## eval smoke against the service (run `make seed` first)
	python3 ../eval/run.py smoke --target $(TARGET)

eval-baseline: ## eval baseline scenario
	python3 ../eval/run.py baseline --target $(TARGET)

eval-burst: ## eval burst scenario
	python3 ../eval/run.py burst --target $(TARGET)

eval-adversarial: ## eval adversarial scenario (restart gateway with high failure first)
	python3 ../eval/run.py adversarial --target $(TARGET)

metrics: ## show intake + delivery-state counters from the API (GET /metrics)
	@curl -fs $(TARGET)/metrics | python3 -m json.tool || echo "(could not reach $(TARGET)/metrics)"

ports: ## show what's listening on :8080 and :9000
	@lsof -nP -iTCP:8080 -sTCP:LISTEN || true
	@lsof -nP -iTCP:9000 -sTCP:LISTEN || true

stop: ## stop the gateway/API (by port) and workers this repo started (by pidfile)
	@lsof -ti tcp:8080 tcp:9000 2>/dev/null | xargs -r kill -9 2>/dev/null || true
	@if [ -f /tmp/notifier_workers.pids ]; then \
		while read -r pid; do ps -p "$$pid" -o command= 2>/dev/null | grep -q worker.py && kill -9 "$$pid" 2>/dev/null || true; done < /tmp/notifier_workers.pids; \
		rm -f /tmp/notifier_workers.pids; \
	fi
	@echo stopped
