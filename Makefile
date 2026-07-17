.PHONY: install run run-agent plan-trip test

VENV := venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev,ai]"

install: $(VENV)

run: $(VENV)
	set -a && . ./setup.env && set +a && $(VENV)/bin/uvicorn app.main:app --reload

run-agent: $(VENV)
	@set -a && . ./setup.env && set +a; \
	command -v rich >/dev/null 2>&1 && export USE_RICH=1 && echo "rich detected — Gemini responses will be rendered as markdown." || true; \
	$(PYTHON) gemini_agent.py

plan-trip: $(VENV)
	@set -a && . ./setup.env && set +a; \
	command -v rich >/dev/null 2>&1 && export USE_RICH=1 && echo "rich detected — Gemini responses will be rendered as markdown." || true; \
	if nc -z localhost 8000 2>/dev/null; then \
		echo "Server already running on :8000 — connecting agent to it."; \
		$(PYTHON) gemini_agent.py; \
	else \
		echo "Starting server on :8000..."; \
		$(VENV)/bin/uvicorn app.main:app & \
		SERVER_PID=$$!; \
		trap "kill $$SERVER_PID 2>/dev/null" EXIT INT TERM; \
		sleep 1; \
		$(PYTHON) gemini_agent.py; \
	fi

test: $(VENV)
	$(VENV)/bin/pytest
