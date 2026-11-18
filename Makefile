.PHONY: test lint run demo web web-dev clean

test:
	python3 -m unittest discover -s tests -v

lint:
	ruff check steward tests

run:
	python3 -m steward

web:
	cd web && npm ci --no-audit --no-fund && npm run build

web-dev:
	cd web && npm run dev

demo:
	python3 scripts/debug_demo.py

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf build dist *.egg-info .ruff_cache web/node_modules
