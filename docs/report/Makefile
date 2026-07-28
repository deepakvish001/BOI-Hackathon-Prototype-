# Build the prototype report.
#
# The numbers come from artifacts/metrics/evaluation.json via
# scripts/render_report_numbers.py, so `make evaluate` at the repository root
# must have been run at least once.

DOC := bodhi_prototype
ROOT := ../..

.PHONY: all numbers clean view

all: numbers $(DOC).pdf

numbers:
	cd $(ROOT) && $(ROOT)/.venv/bin/python scripts/render_report_numbers.py \
	  || python3 scripts/render_report_numbers.py

$(DOC).pdf: $(DOC).tex results.tex refs.bib
	pdflatex -interaction=nonstopmode $(DOC).tex
	-bibtex $(DOC)
	pdflatex -interaction=nonstopmode $(DOC).tex
	pdflatex -interaction=nonstopmode $(DOC).tex

view: $(DOC).pdf
	xdg-open $(DOC).pdf 2>/dev/null || open $(DOC).pdf

clean:
	rm -f *.aux *.log *.bbl *.blg *.out *.toc $(DOC).pdf
