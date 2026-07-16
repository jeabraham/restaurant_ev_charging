.PHONY: install run run-agent test

install:
	python3 -m pip install --upgrade pip
	python3 -m pip install -e .[dev]

run:
	set -a && . ./setup.env && set +a && uvicorn app.main:app --reload

run-agent:
	set -a && . ./setup.env && set +a && python gemini_agent.py

test:
	pytest
