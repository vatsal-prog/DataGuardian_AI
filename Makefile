.PHONY: test serve

test:
	pytest -q

serve:
	python -m dataguardian
