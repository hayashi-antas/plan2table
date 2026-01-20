IMAGE := plan2table
PORT := 7860

ITEM_ID := bm73hxcmcbxk4fnzvdtp6oiose
# fields[].reference の「add more」を %20 にする
PROJECT_ID_REF := op://antas/bm73hxcmcbxk4fnzvdtp6oiose/add more/ceag3cqkcxsoyjcmdkzkmbtalu

.PHONY: build check run

build:
	docker build -t $(IMAGE) .

check:
	@op read '$(PROJECT_ID_REF)' >/dev/null
	@op document get $(ITEM_ID) >/dev/null
	@echo "✔ 1Password secrets OK"

run: check build
	@echo "▶ Loading GCP settings from 1Password"
	docker run --rm -p $(PORT):7860 \
	  -e GOOGLE_CLOUD_PROJECT="$$(op read '$(PROJECT_ID_REF)')" \
	  -e GCP_SERVICE_ACCOUNT_KEY="$$(op document get $(ITEM_ID))" \
	  $(IMAGE)
