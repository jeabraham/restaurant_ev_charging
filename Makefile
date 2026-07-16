.PHONY: install run test

install:
	python -m pip install -e .[dev]

run:
	uvicorn app.main:app --reload

test:
	pytest
