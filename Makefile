.PHONY: dev-up dev-down dev-api test

dev-up:
	./scripts/dev-up.sh

dev-api:
	./scripts/dev-up.sh --with-api

dev-down:
	./scripts/dev-down.sh

test:
	python3 -m unittest discover -s tests -v
