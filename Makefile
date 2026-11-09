SHOP ?= ../tracebench-shop
OUT := out

.PHONY: test lint reproduce

test:
	TRACEBENCH_SHOP=$(abspath $(SHOP)) python3 -m unittest discover -s tests

lint:
	ruff check replaykit tests
	ruff format --check replaykit tests

reproduce:
	mkdir -p $(OUT)
	python3 -m replaykit investigate-app suites/shop-app-v1.json --repo $(abspath $(SHOP)) --replay results/m4/recorded.json --out $(OUT)/report.json > /dev/null
	cmp $(OUT)/report.json results/m4/report.json
	python3 -m replaykit score-app $(OUT)/report.json --validations results/m4/validations.json --out $(OUT)/score.json --md $(OUT)/score.md > /dev/null
	cmp $(OUT)/score.json results/m4/score.json
	@echo "Report and score match results/m4 byte for byte."
