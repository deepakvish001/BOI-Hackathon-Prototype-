PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

.DEFAULT_GOAL := help
.PHONY: help setup data train evaluate demo serve test sample-apk all clean lint

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	 | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

$(BIN)/python:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -r requirements.txt

setup: $(BIN)/python  ## Create the virtualenv and install dependencies

data: setup  ## Simulate the bank (artifacts/data)
	$(BIN)/python scripts/generate_data.py

train: setup  ## Train all layers (artifacts/models)
	$(BIN)/python scripts/train.py

evaluate: setup  ## Evaluate and write artifacts/metrics/evaluation.json
	$(BIN)/python scripts/evaluate.py

sample-apk: setup  ## Build the APK fixtures used by the SHIELD demo
	$(BIN)/python scripts/make_sample_apk.py

boi-demo: setup  ## Alert-dataset track: stand-in data -> train -> predict
	$(BIN)/python -c "from pathlib import Path; from bodhi.boi.synth import generate, SynthConfig, save; \
	  d=Path('artifacts/boi_demo'); d.mkdir(parents=True, exist_ok=True); \
	  save(generate(SynthConfig(n_rows=4000, seed=20260817)), d/'train.parquet'); \
	  save(generate(SynthConfig(n_rows=1500, seed=99)).drop(columns=['FRAUD_TGT']), d/'validation.parquet'); \
	  print('stand-in data written to', d)"
	$(BIN)/python scripts/boi_train.py --train artifacts/boi_demo/train.parquet
	$(BIN)/python scripts/boi_predict.py --model artifacts/boi \
	  --input artifacts/boi_demo/validation.parquet

boi-leakage: setup  ## Measure what the resolution-status columns are worth
	$(BIN)/python scripts/boi_train.py --train artifacts/boi_demo/train.parquet \
	  --allow-leakage --out artifacts/boi_leak

demo: setup  ## Narrated end-to-end walkthrough in the terminal
	$(BIN)/python scripts/demo_stream.py

docs-setup: setup  ## Install the extra dependencies the document builders need
	$(BIN)/pip install -q -r requirements-docs.txt

screenshots: docs-setup  ## Capture the live dashboard (server must be running on :8090)
	$(BIN)/python scripts/capture_screenshots.py

report: docs-setup  ## Build the prototype report (PDF + DOCX + LaTeX macros)
	$(BIN)/python scripts/render_report_numbers.py
	$(BIN)/python scripts/build_report_docx.py
	$(BIN)/python scripts/build_report_pdf.py

deck: docs-setup  ## Build the submission deck (PPTX + PDF)
	$(BIN)/python scripts/build_deck.py
	$(BIN)/python scripts/build_deck_pdf.py

submission: report deck  ## Everything the judges receive
	@echo ""
	@echo "Submission bundle:"
	@ls -1sh docs/BODHI_Mule_Hunter_Deck.pptx docs/BODHI_Mule_Hunter_Deck.pdf \
	         docs/report/BODHI_Mule_Hunter_Prototype_Report.pdf \
	         docs/report/BODHI_Mule_Hunter_Prototype_Report.docx 2>/dev/null

serve: setup  ## Start the API and investigator dashboard on :8000
	$(BIN)/uvicorn bodhi.api.main:app --host 0.0.0.0 --port 8000

test: setup  ## Run the test suite
	$(BIN)/python -m pytest tests -q

all: data train sample-apk evaluate  ## Full pipeline from scratch

.PHONY: docs-setup screenshots report deck submission boi-demo boi-leakage

clean:  ## Remove generated artefacts (keeps metrics and figures)
	rm -rf artifacts/data artifacts/models artifacts/runtime
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache
