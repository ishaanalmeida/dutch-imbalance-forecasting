# All targets go through `uv run`, so no manual venv activation is needed.
# uv resolves the Python 3.11+ required by pyproject.toml on its own.
#
# Windows note: GNU make is not installed by default. Either install it
# (`winget install ezwinports.make`) or run the underlying `uv run ...` command
# directly — every target below is a one-line wrapper on purpose.

UV := uv

.PHONY: install test lint typecheck check fetch features train backtest report repro serve serve-api log-vintage hooks status availability

install:
	$(UV) sync

test:
	$(UV) run pytest

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

typecheck:
	$(UV) run mypy

check: lint typecheck test

hooks:
	$(UV) run pre-commit install

# One-screen view of what is built, what is blocked, and why.
status:
	$(UV) run python -m src.cli status

# The R1 gate made visible for the current ISP.
availability:
	$(UV) run python -m src.cli availability

# CLAUDE.md 7: accrues value with wall-clock time and nothing else does.
# Needs no credentials. Also runs on a 6-hourly cron in CI.
log-vintage:
	$(UV) run python -m src.jobs.log_weather_vintage --forecast-days 7

# Phase 1+ targets. Each fails loudly until the phase that owns it is built,
# so `make repro` can never silently skip a step and still report success.
# Fetchers exist as importable modules (src/data/entsoe.py, openmeteo.py) but
# have no CLI driver yet, and ENTSO-E needs a token. Fails loudly rather than
# appearing to succeed.
fetch:
	$(UV) run python scripts/backfill_history.py

features:
	$(UV) run python -c "from src.features.builder import build_features; print('Feature builder OK')"

train:
	$(UV) run python scripts/run_walkforward_evaluation.py

backtest:
	$(UV) run python scripts/run_backtest.py

holdout:
	OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 $(UV) run python scripts/run_holdout_evaluation.py

report:
	@echo "Reports are pre-built Markdown: README.md, docs/REPORT.md, LIMITATIONS.md"

repro: fetch train backtest holdout
	@echo "All pipeline stages completed. Compare work/ outputs to docs/REPORT.md."

serve:
	$(UV) run streamlit run frontend/app.py --server.headless true

serve-api:
	$(UV) run uvicorn src.api.main:app --host 0.0.0.0 --port 8000
