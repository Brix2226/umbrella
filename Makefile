PYTHON ?= python3
VENV   := .venv
export PYTHONPATH := src:tests

.PHONY: test coverage dev install clean

test:
	$(PYTHON) -m unittest discover -s tests -t tests

dev: $(VENV)/bin/coverage

$(VENV)/bin/coverage:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q coverage

coverage: dev
	$(VENV)/bin/coverage erase
	$(VENV)/bin/coverage run -m unittest discover -s tests -t tests
	$(VENV)/bin/coverage report

install:
	./install.sh

clean:
	rm -rf $(VENV) .coverage htmlcov build *.egg-info src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
