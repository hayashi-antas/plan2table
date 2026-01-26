IMAGE := plan2table
PORT := 7860

ITEM_ID := bm73hxcmcbxk4fnzvdtp6oiose
# fields[].reference の「add more」を %20 にする
PROJECT_ID_REF := op://antas/bm73hxcmcbxk4fnzvdtp6oiose/add more/ceag3cqkcxsoyjcmdkzkmbtalu

# Vertex AI settings (override via environment or command line, e.g. make run VERTEX_LOCATION=asia-northeast1)
VERTEX_LOCATION ?= global
VERTEX_MODEL_NAME ?= gemini-3-pro-preview

.PHONY: build check run

build:
	docker build -t $(IMAGE) .

check:
	@op read '$(PROJECT_ID_REF)' >/dev/null
	@op document get $(ITEM_ID) >/dev/null
	@echo "✔ 1Password secrets OK"

run: build
	@echo "▶ Loading GCP settings from 1Password"
	docker run --rm -p $(PORT):7860 \
	  -e GOOGLE_CLOUD_PROJECT="$$(op read '$(PROJECT_ID_REF)')" \
	  -e GCP_SERVICE_ACCOUNT_KEY="$$(op document get $(ITEM_ID))" \
	  -e VERTEX_LOCATION="$(VERTEX_LOCATION)" \
	  -e VERTEX_MODEL_NAME="$(VERTEX_MODEL_NAME)" \
	  $(IMAGE)
