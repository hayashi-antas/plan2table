IMAGE := plan2table
PORT := 7860

# 1Password references (op read で読み取る)
PROJECT_ID_REF := op://antas/me check service account json key file/add more/project ID
GCP_SERVICE_ACCOUNT_REF := op://antas/me check service account json key file/me-check-487106-61fe11f85a91.json

# Vertex AI settings (override via environment or command line, e.g. make run VERTEX_LOCATION=asia-northeast1)
VERTEX_LOCATION ?= global
VERTEX_MODEL_NAME ?= gemini-3.1-pro-preview

# test/lint/format は Docker 内で実行（ビルド済みイメージを使用）
DOCKER_RUN := docker run --rm -v "$$(pwd):/app" -w /app $(IMAGE)

.PHONY: build check run lint format format-check check-all test install-hooks

# Install git pre-push hook that runs make check-all (optional, for local dev)
install-hooks:
	@mkdir -p .git/hooks
	@cp scripts/pre-push .git/hooks/pre-push
	@chmod +x .git/hooks/pre-push
	@echo "✔ Installed pre-push hook (runs make check-all before push)"

build:
	docker build -t $(IMAGE) .

check:
	@op read '$(PROJECT_ID_REF)' >/dev/null
	@op read '$(GCP_SERVICE_ACCOUNT_REF)' >/dev/null
	@echo "✔ 1Password secrets OK"

run: build
	@echo "▶ Loading GCP settings from 1Password (Vertex AI + Vision API)"
	docker run --rm -p $(PORT):7860 \
	  -e GOOGLE_CLOUD_PROJECT="$$(op read '$(PROJECT_ID_REF)')" \
	  -e GCP_SERVICE_ACCOUNT_KEY="$$(op read '$(GCP_SERVICE_ACCOUNT_REF)')" \
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
