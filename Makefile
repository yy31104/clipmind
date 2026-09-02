PYTHON ?= .venv/bin/python
OUT ?= out

.PHONY: test eval bench run

test:
	node --check web/app.js
	$(PYTHON) -m unittest discover -s tests -v

eval:
	$(PYTHON) scripts/evaluate.py --out $(OUT)
	$(PYTHON) scripts/evaluate_silent_slides.py

bench:
	$(PYTHON) scripts/benchmark.py

run:
	$(PYTHON) run.py
