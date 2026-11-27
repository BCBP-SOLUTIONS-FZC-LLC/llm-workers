# -----------------------------
# CONFIG
# -----------------------------
APP_NAME      ?= llm-workers
PKG           ?= llm-workers-generation
GEN           ?= generation
VENV          ?= .venv
PY            ?= $(VENV)/bin/python
# uv from PATH, else the copy `make setup` installs into the venv.
UV            ?= $(shell command -v uv 2>/dev/null || echo $(VENV)/bin/uv)
BUILD_VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
IMAGE         ?= llm-workers-generation:dev

export APP_NAME BUILD_VERSION

TRIVY_IMAGE ?= ghcr.io/aquasecurity/trivy:latest
# the sources every quality gate looks at
CODE := $(GEN)/src $(GEN)/tests $(GEN)/real_engine

# -----------------------------
# SETUP
# -----------------------------

.PHONY: setup
setup:
	@test -f .env || { test -f .env-example && cp .env-example .env || true; }
	@command -v uv >/dev/null 2>&1 || test -x $(VENV)/bin/uv || { \
	  test -x $(PY) || python3 -m venv $(VENV); \
	  echo "uv not found: installing into $(VENV)"; \
	  $(PY) -m pip install -q 'uv==0.13.6'; }
	@# uv may replace the venv with one on the interpreter requires-python asks for (3.15), and that venv
	@# has no uv in it: run sync from a copy, then put the pinned uv back. The engine is a private git
	@# dependency (ssh): your ssh key must be able to read BCBP-SOLUTIONS-FZC-LLC/llm-retrieval-generation.
	@tmp=$$(mktemp) && cp "$(UV)" "$$tmp" && chmod +x "$$tmp" && "$$tmp" sync --locked --package $(PKG) --group dev && \
	  { test -x $(VENV)/bin/uv || "$$tmp" pip install -q --python $(PY) 'uv==0.13.6'; }; rc=$$?; rm -f "$$tmp"; exit $$rc
	@$(MAKE) --no-print-directory install-hooks
	@echo "Environment ready ($(VENV))"

.PHONY: install-hooks
install-hooks:
	@if [ -d .git ]; then \
	  mkdir -p .git/hooks && cp .githooks/pre-commit .githooks/pre-push .git/hooks/ \
	    && chmod +x .git/hooks/pre-commit .git/hooks/pre-push && echo "Installed git hooks"; \
	else echo "not a git checkout; skipping hooks"; fi

.PHONY: help
help:
	@echo "Available commands:"
	@echo "  make setup           - create .venv and install deps (uv sync); copy .env-example; install git hooks"
	@echo "  make install-hooks   - install .githooks/pre-commit and pre-push into .git/hooks"
	@echo "  make lock            - refresh the lockfile (uv lock)"
	@echo "  make lock-check      - fail if uv.lock is stale vs pyproject.toml"
	@echo "  make fmt             - ruff autofix + format"
	@echo "  make fmt-check       - verify ruff formatting (mirrors CI)"
	@echo "  make lint            - ruff check + format check"
	@echo "  make typecheck       - mypy --strict on $(GEN)/src"
	@echo "  make arch            - architecture rules (WD-1..7, WB-1) and the engine contract tests"
	@echo "  make replay          - replay every recorded workflow history against the current code (WV-3)"
	@echo "  make test            - the suite without the integration marker"
	@echo "  make test-ci         - the whole suite with coverage (used in CI; downloads the Temporal test server)"
	@echo "  make cover-check     - test-ci, then enforce the coverage gate (COVERAGE_THRESHOLD, default 95)"
	@echo "  make build           - build the wheel and sdist (uv build) into dist/"
	@echo "  make real-engine-test - the adapter against the real engine on throwaway Postgres, S3 and Valkey (Docker)"
	@echo "  make metrics-lint    - the platform metric linter over both real scrapes (Observability Standard)"
	@echo "  make metrics-inventory - regenerate docs/platform-observability-inventory.md from the pinned library"
	@echo "  make chart-check     - helm lint and render for dev, staging and prod, validated with kubeconform"
	@echo "  make ci              - lock-check + fmt-check + lint + typecheck + arch + replay + metrics-lint + chart-check + test-ci + build"
	@echo "  make vuln-check      - pip-audit on the locked deps"
	@echo "  make trivy           - build the image, then CVE-scan it as release.yml does (CRITICAL,HIGH, fixed only)"
	@echo "  make clean           - remove build artefacts"

# -----------------------------
# PYTHON BASICS
# -----------------------------

.PHONY: lock
lock:
	$(UV) lock

.PHONY: lock-check
lock-check:
	$(UV) lock --check

.PHONY: fmt
fmt:
	$(PY) -m ruff check --fix $(CODE)
	$(PY) -m ruff format $(CODE)

.PHONY: fmt-check
fmt-check:
	$(PY) -m ruff format --check $(CODE)
	@echo "ruff format: all files formatted"

.PHONY: lint
lint:
	@echo "Running linter..."
	$(PY) -m ruff check $(CODE)
	$(PY) -m ruff format --check $(CODE)

.PHONY: typecheck
typecheck:
	$(PY) -m mypy $(GEN)/src

.PHONY: vuln-check
vuln-check:
	$(UV) run --with pip-audit pip-audit --skip-editable

# -----------------------------
# TESTS
# -----------------------------

# WD-1..WD-7 and WB-1 (the layer and boundary rules), and the engine's recorded API shape (WB-4).
.PHONY: arch
arch:
	$(PY) -m pytest --no-cov $(GEN)/tests/unit/test_architecture.py $(GEN)/tests/unit/test_engine_contract.py $(GEN)/tests/unit/test_real_engine.py

# WV-3: recorded histories of every workflow type still replay against the current code.
.PHONY: replay
replay:
	$(PY) -m pytest --no-cov $(GEN)/tests/replay

# The workers' adapter and activities against the REAL rag.engine on throwaway Postgres 18, an S3
# emulator (floci) and Valkey, each started by the test through the docker CLI (random localhost
# ports, removed afterwards; nothing shared is touched). Needs Docker and read access to the engine
# repository: the engine tag is cloned once into ENGINE_TAG_DIR for its migrations (keep ENGINE_TAG
# equal to the tag in generation/pyproject.toml). About 6 s once the images are pulled. Not part of
# `make ci`: it needs Docker.
ENGINE_TAG     ?= engine-v1.1.0
ENGINE_TAG_DIR ?= .cache/engine-tag
.PHONY: engine-tag-clone
engine-tag-clone:
	@test -d $(ENGINE_TAG_DIR)/migrations || git clone --quiet --depth 1 --branch $(ENGINE_TAG) \
	  ssh://git@github.com/BCBP-SOLUTIONS-FZC-LLC/llm-retrieval-generation.git $(ENGINE_TAG_DIR)

.PHONY: real-engine-test
real-engine-test: engine-tag-clone
	ENGINE_TAG_DIR=$(abspath $(ENGINE_TAG_DIR)) $(PY) -m pytest --no-cov -m "integration or not integration" $(GEN)/real_engine

# The Observability Standard: the platform's metric linter over the real scrape of both endpoints
# (the ops app and the Temporal runtime) of a worker on a dev server (real clock). Needs the dev
# server download on first use. test-ci runs the same test again, for coverage.
.PHONY: metrics-lint
metrics-lint:
	$(PY) -m pytest --no-cov $(GEN)/tests/integration/test_metrics_lint.py -m "integration or not integration"

# The Helm chart: lint and render each environment's values, then validate the manifests against the
# Kubernetes schemas (kubeconform; the CRDs of KEDA and the Prometheus Operator have no schema here and are
# skipped). The values below are placeholders for what the platform supplies at install time.
CHART      ?= deploy/helm/llm-workers
CHART_SET  := --set image.tag=chart-check --set temporal.address=temporal.invalid:7233 \
              --set codecKey.keyId=k1 --set autoscaling.kedaTemporalScaler=keda-scaler.invalid:6000
.PHONY: chart-check
chart-check:
	@command -v helm >/dev/null || { echo "helm is required"; exit 1; }
	@command -v kubeconform >/dev/null || { echo "kubeconform is required"; exit 1; }
	@for e in dev staging prod; do \
	  helm lint $(CHART) -f $(CHART)/values-$$e.yaml $(CHART_SET) >/dev/null || exit 1; \
	  helm template llm-workers $(CHART) -f $(CHART)/values-$$e.yaml $(CHART_SET) --set podMonitor.enabled=true --set alerts.enabled=true \
	    | kubeconform -strict -ignore-missing-schemas -summary -kubernetes-version 1.30.0 || exit 1; \
	done

# The platform registry's inventory (generated by the pinned fastapicommon; not this service's list).
.PHONY: metrics-inventory
metrics-inventory:
	.venv/bin/fastapicommon-metricslint inventory -o docs/platform-observability-inventory.md

.PHONY: test
test:  ## unit, workflow (time-skipping), replay and security tests; the integration marker is off
	$(PY) -m pytest $(GEN)/tests

.PHONY: test-ci
test-ci:  ## everything, one coverage run
	$(PY) -m pytest $(GEN)/tests -m "integration or not integration" --cov=llm_workers_generation --cov-report=xml:coverage.xml --cov-report=term

.PHONY: cover-check
cover-check: test-ci
	PY=$(PY) bash .github/scripts/coverage-gate.sh

# -----------------------------
# CI
# -----------------------------

.PHONY: ci
ci: lock-check fmt-check lint typecheck arch replay metrics-lint chart-check test-ci build

# -----------------------------
# BUILD
# -----------------------------

.PHONY: build
build:
	$(UV) build --package $(PKG) --out-dir dist

# the service image. The engine is a private git dependency, read over https with a token passed as a
# BuildKit secret: GO_PRIVATE_TOKEN (the secret name the IAM repo uses), else your `gh auth token`.
.PHONY: docker-build
docker-build:
	GO_PRIVATE_TOKEN="$${GO_PRIVATE_TOKEN:-$$(gh auth token)}" docker build --secret id=gh_token,env=GO_PRIVATE_TOKEN --build-arg BUILD_VERSION=$(BUILD_VERSION) -f Dockerfile -t $(IMAGE) .

# trivy: the release.yml CVE gate, locally. Scans the image docker-build just made for CRITICAL/HIGH
# findings that have a fix; exits non-zero on any. Uses a local trivy if installed, else the
# TRIVY_IMAGE container (the Docker socket is mounted so it can read the local image).
.PHONY: trivy
trivy: docker-build
	@if command -v trivy >/dev/null 2>&1; then \
	  trivy image --ignore-unfixed --vuln-type os,library --severity CRITICAL,HIGH --exit-code 1 $(IMAGE); \
	 else \
	  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v $$HOME/.cache/trivy:/root/.cache/ -v $(CURDIR)/.trivyignore:/.trivyignore:ro "$(TRIVY_IMAGE)" \
	    image --ignore-unfixed --vuln-type os,library --severity CRITICAL,HIGH --exit-code 1 $(IMAGE); \
	 fi

# -----------------------------
# CLEAN
# -----------------------------

.PHONY: clean
clean:
	rm -rf htmlcov dist .pytest_cache .mypy_cache .ruff_cache $(GEN)/.pytest_cache $(GEN)/.mypy_cache $(GEN)/.ruff_cache
	rm -f coverage.xml .coverage $(GEN)/.coverage
