PYTHON ?= .venv/bin/python
OUT ?= out

.PHONY: test eval eval-reextract eval-synthetic bench run

test:
	node --check web/app.js
	$(PYTHON) -m unittest discover -s tests -v

eval:
	$(PYTHON) scripts/evaluate.py --mode existing --out $(OUT)

eval-reextract:
	$(PYTHON) scripts/evaluate.py --mode reextract

eval-synthetic:
	$(PYTHON) scripts/evaluate_silent_slides.py

bench:
	$(PYTHON) scripts/benchmark.py

run:
	$(PYTHON) run.py
