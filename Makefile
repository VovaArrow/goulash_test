.PHONY: install run test seed

install:
	uv sync

run:
	uv run uvicorn app.main:app --reload --port 8000

test:
	uv run pytest -q

seed:
	rm -f data/goulash.db && uv run python -c "from app.db import connect, init_db; from app.seed import seed_db; c = connect('data/goulash.db'); init_db(c); seed_db(c)"
