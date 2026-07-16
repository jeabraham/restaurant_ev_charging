.PHONY: install run test

install:
	python -m pip install -e .[dev]

run:
	set -a && . ./setup.env && set +a && uvicorn app.main:app --reload

test:
	pytest
