.PHONY: build build-alpine build-bootstrap build-docs build-playwright \
  build-ts bump-bootstrap certs check check-extra check-light commit \
  docs generate-version ipython manual run-alpine run-playwright \
  run-playwright-slow serve serve-local sync sync-all update-deps

BIND ?= 127.0.0.1:8080
IMAGE ?= tooling-trusted-release
STATE_DIR ?= state

build: build-alpine

build-alpine:
	scripts/build $(IMAGE)

build-bootstrap:
	docker build -t atr-bootstrap bootstrap/context
	docker run --rm \
	  -v "$$PWD/bootstrap/source:/opt/bootstrap/source" \
	  -v "$$PWD/atr/static:/run/bootstrap-output" \
	  atr-bootstrap

build-docs:
	mkdir -p docs
	rm -f docs/*.html
	uv run --frozen python3 scripts/docs_build.py
	for fn in atr/docs/*.md; do out=$${fn#atr/}; cmark "$$fn" > "$${out%.md}.html"; done
	uv run --frozen python3 scripts/docs_post_process.py docs/*.html

build-playwright:
	docker build -t atr-playwright -f tests/Dockerfile.playwright playwright

build-ts:
	tsgo --project ./tsconfig.json

bump-bootstrap:
	@test -n "$(BOOTSTRAP_VERSION)" \
	  || { echo "usage: make bump-bootstrap BOOTSTRAP_VERSION=X.Y.Z"; exit 1; }
	docker build -t atr-bootstrap bootstrap/context
	docker run --rm \
	  -v "$$PWD/bootstrap/source:/opt/bootstrap/source" \
	  atr-bootstrap /opt/bootstrap/bump.sh $(BOOTSTRAP_VERSION)

certs:
	if test ! -f $(STATE_DIR)/hypercorn/secrets/cert.pem || test ! -f $(STATE_DIR)/hypercorn/secrets/key.pem; \
	then STATE_DIR=$(STATE_DIR) uv run --frozen scripts/generate-certificates; \
	fi

certs-local:
	mkdir -p $(STATE_DIR)/hypercorn/secrets
	cd $(STATE_DIR)/hypercorn/secrets && umask 277 && mkcert localhost.apache.org 127.0.0.1 ::1

check:
	git add -A
	uv run --frozen pre-commit run --all-files

check-extra:
	@git add -A
	@find atr -name '*.py' -exec python3 scripts/interface_order.py {} --quiet \;
	@find atr -name '*.py' -exec python3 scripts/interface_privacy.py {} --quiet \;

check-heavy:
	git add -A
	uv run --frozen pre-commit run --all-files --config .pre-commit-heavy.yaml

check-light:
	git add -A
	uv run --frozen pre-commit run --all-files --config .pre-commit-light.yaml

commit:
	git add -A
	git commit
	git pull
	git push

docs:
	mkdir -p docs
	uv run --frozen python3 scripts/docs_check.py
	rm -f docs/*.html
	uv run --frozen python3 scripts/docs_build.py
	for fn in atr/docs/*.md; do out=$${fn#atr/}; cmark "$$fn" > "$${out%.md}.html"; done
	uv run --frozen python3 scripts/docs_post_process.py docs/*.html
	uv run --frozen python3 scripts/docs_check.py

generate-version:
	@rm -f atr/version.py
	@uv run --frozen python3 atr/metadata.py > /tmp/version.py
	@mv /tmp/version.py atr/version.py
	@cat atr/version.py

ipython:
	uv run --frozen --with ipython ipython

run-alpine:
	docker run --rm --init --user "$$(id -u):$$(id -g)" \
	  -p 8080:8080 -p 2222:2222 \
	  -v "$$PWD/$(STATE_DIR):/opt/atr/state" \
	  -v "$$PWD/$(STATE_DIR)/hypercorn/secrets/localhost.apache.org+2-key.pem:/opt/atr/state/hypercorn/secrets/key.pem" \
	  -v "$$PWD/$(STATE_DIR)/hypercorn/secrets/localhost.apache.org+2.pem:/opt/atr/state/hypercorn/secrets/cert.pem" \
	  -e APP_HOST=localhost.apache.org:8080 -e ALLOW_TESTS=1 \
	  -e SSH_HOST=0.0.0.0 -e BIND=0.0.0.0:8080 \
	  tooling-trusted-release

run-playwright:
	docker run --net=host -it atr-playwright python3 test.py --skip-slow

run-playwright-slow:
	docker run --net=host -it atr-playwright python3 test.py --tidy

serve:
	@scripts/check-certs
	@scripts/check-perms
	SSH_HOST=127.0.0.1 uv run --frozen hypercorn --bind $(BIND) \
	  --keyfile hypercorn/secrets/localhost.apache.org+2-key.pem \
	  --certfile hypercorn/secrets/localhost.apache.org+2.pem \
	  atr.server:app --debug --reload --worker-class uvloop

serve-local:
	@scripts/check-certs
	@scripts/check-perms
	APP_HOST=localhost.apache.org:8080 DISABLE_CHECK_CACHE=1 ALLOW_TESTS=1 \
	  SSH_HOST=127.0.0.1 uv run --frozen hypercorn --bind $(BIND) \
	  --keyfile hypercorn/secrets/localhost.apache.org+2-key.pem \
	  --certfile hypercorn/secrets/localhost.apache.org+2.pem \
	  atr.server:app --debug --reload --worker-class uvloop

sync:
	uv sync --frozen --no-dev

sync-all:
	uv sync --frozen --all-groups

update-deps:
	pre-commit autoupdate || :
	uv lock --upgrade --exclude-newer "$$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	uv sync --frozen --all-groups
