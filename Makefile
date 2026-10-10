.PHONY: test compileall build run doctor

test:
	python3 -m unittest discover -s tests -p 'test_*.py' -v

compileall:
	python3 -m compileall -q brio_control scripts

build: compileall
	python3 scripts/build_deb.py

run:
	scripts/run-local gui

doctor:
	scripts/run-local doctor
