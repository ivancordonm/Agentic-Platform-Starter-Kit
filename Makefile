.PHONY: install api ui dev docker-up docker-down validate test lint typecheck check

install:
	uv sync --locked

api:
	uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

ui:
	uv run streamlit run app/ui/streamlit_app.py --server.address 127.0.0.1 --server.port 8501

dev:
	sh scripts/dev.sh

docker-up:
	docker compose up --build

docker-down:
	docker compose down

validate:
	uv run python scripts/validate_project.py

test:
	uv run pytest -q

lint:
	uv run ruff check .

typecheck:
	uv run mypy app tests

check: lint typecheck test
