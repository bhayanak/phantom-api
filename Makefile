# Development and security tasks.
#
# The security targets are the same checks CI runs, so a clean `make security`
# locally means a clean pipeline.

.PHONY: help install test lint format security scan-deps scan-image scan-config \
        scan-secrets scan-corpora build clean

IMAGE ?= phantom-api:local
PYTHON ?= python
# Findings at or above this level fail a scan.
SEVERITY ?= CRITICAL,HIGH

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install the package and development dependencies
	$(PYTHON) -m pip install -e '.[dev,mock]'

test: ## Run the test suite with the coverage gate
	$(PYTHON) -m pytest -q --cov=src/phantom_api --cov-fail-under=95

lint: ## Check formatting and lint rules
	ruff check src/ tests/
	ruff format --check src/ tests/

format: ## Apply formatting and safe lint fixes
	ruff check --fix src/ tests/
	ruff format src/ tests/

build: ## Build the container image
	docker build -t $(IMAGE) .

# -- security ---------------------------------------------------------------

security: scan-deps scan-config scan-secrets scan-image scan-corpora ## Run every security check
	@echo "\nAll security checks passed."

scan-deps: ## Dependency vulnerabilities (Trivy filesystem + pip-audit)
	@echo "== dependencies"
	trivy fs --scanners vuln --severity $(SEVERITY) --exit-code 1 --quiet .
	@$(PYTHON) -m pip_audit --version >/dev/null 2>&1 || $(PYTHON) -m pip install -q pip-audit
	$(PYTHON) -m pip_audit --skip-editable

scan-config: ## Dockerfile and compose misconfiguration
	@echo "== configuration"
	trivy config --severity $(SEVERITY) --exit-code 1 --quiet .

scan-secrets: ## Secrets in the working tree
	@echo "== secrets"
	trivy fs --scanners secret --exit-code 1 --quiet .

scan-image: build ## Container vulnerabilities
	@echo "== container image"
	trivy image --scanners vuln --severity $(SEVERITY) --exit-code 1 --quiet --ignore-unfixed=false $(IMAGE)

scan-corpora: ## The published corpora are safe to ship
	@echo "== published corpora"
	@tmp=$$(mktemp -d); \
	phantom-api mock sanitize examples/vcenter/corpus --out $$tmp/vcenter \
		--terms-file examples/vcenter/tools/vendor-terms.txt >/dev/null || exit 1; \
	phantom-api mock sanitize examples/morpheus/corpus --out $$tmp/morpheus \
		--rules examples/morpheus/tools/rules.yaml \
		--terms-file examples/morpheus/tools/site-terms.txt >/dev/null || exit 1; \
	rm -rf $$tmp; \
	$(PYTHON) scripts/check_no_credentials.py

clean: ## Remove build artefacts
	rm -rf dist build .coverage coverage.xml .pytest_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
