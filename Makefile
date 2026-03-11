IMAGE := plan2table
PORT := 7860

# 1Password references (op read で読み取る)
PROJECT_ID_REF := op://antas/me check service account json key file/add more/project ID
GCP_SERVICE_ACCOUNT_REF := op://antas/me check service account json key file/me-check-487106-61fe11f85a91.json

# Vertex AI settings (override via environment or command line, e.g. make run VERTEX_LOCATION=asia-northeast1)
VERTEX_LOCATION ?= global
VERTEX_MODEL_NAME ?= gemini-3.1-pro-preview

# test/lint/format は Docker 内で実行（ビルド済みイメージを使用）。--user でホストの UID/GID にしマウント先のファイルが root 所有にならないようにする
DOCKER_RUN := docker run --rm -v "$$(pwd):/app" -w /app --user "$$(id -u):$$(id -g)" $(IMAGE)

.PHONY: build check run lint format format-check check-all test install-hooks

# Install git pre-commit hook that runs make lint and make format (optional, for local dev).
# Do not overwrite an existing pre-commit hook.
install-hooks:
	@mkdir -p .git/hooks
	@if [ -f .git/hooks/pre-commit ]; then \
		echo "Error: .git/hooks/pre-commit already exists. Remove or rename it first."; \
		exit 1; \
	fi
	@cp scripts/pre-commit .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "✔ Installed pre-commit hook (runs make lint and make format before commit)"

build:
	docker build -t $(IMAGE) .

check:
	@op read '$(PROJECT_ID_REF)' >/dev/null
	@op read '$(GCP_SERVICE_ACCOUNT_REF)' >/dev/null
	@echo "✔ 1Password secrets OK"

run: build
	@echo "▶ Loading GCP settings from 1Password (Vertex AI + Vision API)"
	@set -e; \
	PROJECT_ID=$$(op read '$(PROJECT_ID_REF)'); \
	test -n "$$PROJECT_ID" || { echo "Error: GOOGLE_CLOUD_PROJECT is empty or op read failed"; exit 1; }; \
	GCP_KEY=$$(op read '$(GCP_SERVICE_ACCOUNT_REF)'); \
	test -n "$$GCP_KEY" || { echo "Error: GCP_SERVICE_ACCOUNT_KEY is empty or op read failed"; exit 1; }; \
	docker run --rm -p $(PORT):7860 \
	  -e GOOGLE_CLOUD_PROJECT="$$PROJECT_ID" \
	  -e GCP_SERVICE_ACCOUNT_KEY="$$GCP_KEY" \
	  -e VERTEX_LOCATION="$(VERTEX_LOCATION)" \
	  -e VERTEX_MODEL_NAME="$(VERTEX_MODEL_NAME)" \
	  $(IMAGE)

# Test (Docker 内で実行; カレントのソースをマウント). 初回や Dockerfile/requirements 変更時は make build を先に実行
test:
	$(DOCKER_RUN) env PYTHONPATH=/app pytest -v

# Lint and format (Docker 内で実行). Black は py313 指定で except (A,B) の括弧を維持（py314 だと PEP 758 で括弧を外す）
lint:
	$(DOCKER_RUN) ruff check .

format:
	$(DOCKER_RUN) black --target-version py313 .

format-check:
	$(DOCKER_RUN) black --target-version py313 --check .

check-all:
	$(DOCKER_RUN) ruff check . && $(DOCKER_RUN) black --target-version py313 --check .
	@echo "✔ lint and format check passed"
