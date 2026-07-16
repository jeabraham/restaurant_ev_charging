.PHONY: install run run-agent test

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
	set -a && . ./setup.env && set +a && $(PYTHON) gemini_agent.py

test: $(VENV)
	$(VENV)/bin/pytest
