# All targets go through `uv run`, so no manual venv activation is needed.
# uv resolves the Python 3.11+ required by pyproject.toml on its own.
#
# Windows note: GNU make is not installed by default. Either install it
# (`winget install ezwinports.make`) or run the underlying `uv run ...` command
# directly — every target below is a one-line wrapper on purpose.

UV := uv

.PHONY: install test lint typecheck check fetch features train backtest report repro serve

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

# Phase 1+ targets. Each fails loudly until the phase that owns it is built,
# so `make repro` can never silently skip a step and still report success.
# Fetchers exist as importable modules (src/data/entsoe.py, openmeteo.py) but
# have no CLI driver yet, and ENTSO-E needs a token. Fails loudly rather than
# appearing to succeed.
fetch:
	@echo "No fetch driver yet: fetchers are importable modules; ENTSOE_API_TOKEN also required" && exit 1

features:
	@echo "TODO: Phase 2 not built yet (feature builder)" && exit 1

train:
	@echo "TODO: Phase 2 not built yet" && exit 1

backtest:
	@echo "TODO: Phase 4 not built yet" && exit 1

report:
	@echo "TODO: Phase 6 not built yet" && exit 1

repro: fetch features train backtest report

serve:
	@echo "TODO: Phase 5 not built yet" && exit 1
