# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

pytest-bdd is a BDD (Behavior-Driven Development) framework that integrates Gherkin feature files with pytest. It parses `.feature` files using the official `gherkin-official` library and maps Gherkin steps to Python functions via decorators, injecting them as pytest fixtures.

## Development Commands

```bash
# Install dependencies
poetry install

# Run all tests
poetry run pytest

# Run a single test file
poetry run pytest tests/steps/test_steps.py

# Run a single test by name
poetry run pytest tests/steps/test_steps.py -k "test_name"

# Run type checking
tox -e mypy

# Run linting/formatting (ruff)
poetry run ruff check src/
poetry run ruff format src/

# Run full tox matrix
tox

# Run specific tox environment
tox -e py313-pytest83-gherkin37
```

## Architecture

Source is in `src/pytest_bdd/`. The data flow is:

1. **`plugin.py`** — pytest plugin entry point; registers CLI options, fixtures, and hooks
2. **`feature.py`** → **`gherkin_parser.py`** → **`parser.py`** — loading chain: `feature.py` caches feature files, `gherkin_parser.py` wraps `gherkin-official` with error handling, `parser.py` converts parsed Gherkin AST into internal `Feature`/`Scenario`/`Step`/`Background` dataclasses
3. **`steps.py`** — `@given`, `@when`, `@then`, `@step` decorators that register step functions in a `WeakKeyDictionary` keyed by pytest `Session`
4. **`parsers.py`** — Step parameter parsers (`string`, `re`, `parse`, `cfparse`); these are the backends for extracting named parameters from step text
5. **`scenario.py`** — Core execution engine: matches step text to registered functions using `StepParser`, resolves pytest fixtures for each step, handles parametrization for Scenario Outlines, and drives the step-by-step execution loop
6. **`reporting.py`** / **`gherkin_terminal_reporter.py`** / **`cucumber_json.py`** — Collect timing/status and emit reports in Gherkin terminal or Cucumber JSON formats
7. **`hooks.py`** — `pytest_bdd_*` hookspecs (`before_scenario`, `after_scenario`, `before_step`, `after_step`, `step_error`)
8. **`generation.py`** — Generates Python test stubs from feature files via Mako templates (`--generate-missing` CLI flag)

## Key Design Patterns

- **Steps as fixtures**: each matched step function is invoked through pytest's fixture injection machinery; step arguments become fixture parameters
- **WeakKeyDictionary registries**: step registrations are stored keyed on the pytest `Session` to avoid cross-test pollution
- **StepParser polymorphism**: `string`, `re`, `parse`, and `cfparse` all implement the same `StepParser` interface used in `scenario.py` for matching
- **gherkin-official dependency**: supported versions are 29–37 (tested across the tox matrix); the `gherkin_parser.py` layer normalises API differences

## Test Layout

Tests live in `tests/` with feature files (`.feature`) co-located alongside their Python test modules. The `pytest.ini` sets `testpaths = tests`. Feature files under `tests/parser/test.feature` serve as fixtures demonstrating Background, Scenario Outline, Rules, Data Tables, and Doc Strings.

## Configuration

- **`pyproject.toml`** — project metadata, ruff rules, mypy settings, coverage config
- **`tox.ini`** — test matrix: Python 3.9–3.13 × pytest 7.0–8.3 × gherkin-official 29–37
- **`pytest.ini`** — testpaths and warning filters for the package's own tests
