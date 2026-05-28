# Convenience targets. Requires Python 3.10+, no pip dependencies.

PYTHON ?= python3
GATEWAY_URL ?= http://localhost:9000
SERVICE_URL ?= http://localhost:8080

.PHONY: help gateway example smoke baseline burst adversarial reset health

help:
	@echo "Targets:"
	@echo "  gateway        run the mock push gateway on :9000"
	@echo "  example        run the example stub service on :8080 (replace with yours)"
	@echo "  smoke          run the smoke scenario against \$$SERVICE_URL"
	@echo "  baseline       run the baseline scenario"
	@echo "  burst          run the burst scenario"
	@echo "  adversarial    run the adversarial scenario (restart gateway with high failure rates first)"
	@echo "  reset          clear the gateway's in-memory state"
	@echo "  health         check the gateway is up"
	@echo
	@echo "Override SERVICE_URL=... to point eval at your service."

gateway:
	$(PYTHON) mock_gateway/server.py

example:
	$(PYTHON) example_solution/service.py

smoke:
	$(PYTHON) eval/run.py smoke --target $(SERVICE_URL)

baseline:
	$(PYTHON) eval/run.py baseline --target $(SERVICE_URL)

burst:
	$(PYTHON) eval/run.py burst --target $(SERVICE_URL)

adversarial:
	@echo "Make sure the gateway is restarted with:"
	@echo "  FAIL_5XX_RATE=0.3 DROP_RATE=0.1 make gateway"
	$(PYTHON) eval/run.py adversarial --target $(SERVICE_URL)

reset:
	curl -s -X POST $(GATEWAY_URL)/_reset; echo

health:
	curl -s $(GATEWAY_URL)/_health; echo
