PYTHON ?= .venv/bin/python
OUT ?= out

.PHONY: test eval eval-reextract eval-synthetic eval-noisy eval-noisy-decode bench run

test:
	node --check clipmind/web/app.js
	$(PYTHON) -m unittest discover -s tests -v

eval:
	$(PYTHON) scripts/evaluate.py --mode existing --out $(OUT)

eval-reextract:
	$(PYTHON) scripts/evaluate.py --mode reextract

eval-synthetic:
	$(PYTHON) scripts/evaluate_silent_slides.py

eval-noisy:
	$(PYTHON) scripts/evaluate_noisy_slides.py --sweep

eval-noisy-decode:
	$(PYTHON) scripts/evaluate_noisy_slides.py --decode --sweep

bench:
	$(PYTHON) scripts/benchmark.py

run:
	$(PYTHON) run.py
