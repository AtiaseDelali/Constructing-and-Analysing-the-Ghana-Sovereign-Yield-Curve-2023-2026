# Ghana sovereign yield curve pipeline
PY ?= python

.PHONY: all download parse dataset fit analyse report report-md clean

all: parse dataset fit analyse

download:
	$(PY) src/gfim_download.py 2023-2026 --no-guess

parse:
	$(PY) src/parse_reports.py

dataset:
	$(PY) src/build_dataset.py

fit:
	$(PY) src/fit_curves.py

analyse:
	$(PY) src/analyse_curves.py

report:
	Rscript -e 'rmarkdown::render("analysis/ghana_yield_curve_report.Rmd", output_dir = "docs", output_file = "index.html")'

report-md:
	Rscript -e 'rmarkdown::render("analysis/ghana_yield_curve_report.Rmd", output_format = bookdown::github_document2(number_sections = TRUE, toc = TRUE), output_dir = ".", output_file = "REPORT.md")'

clean:
	rm -rf data/processed/*.pkl analysis/*_cache analysis/*_files
