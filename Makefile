.PHONY: test lint run clean

test:
	python3 -m unittest discover -s tests -v

lint:
	ruff check steward tests

run:
	python3 -m steward

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf build dist *.egg-info .ruff_cache
