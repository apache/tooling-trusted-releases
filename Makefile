.PHONY: build build-alpine build-bootstrap build-docs build-playwright \
  build-ts bump-bootstrap certs check check-clean check-extra check-heavy \
  check-light commit docs e2e e2e-clean generate-version ipython manual \
  run-alpine run-playwright run-playwright-slow serve serve-local \
  svn-dev-repo sync sync-all unit update-deps

BIND ?= 127.0.0.1:8080
IMAGE ?= tooling-trusted-release
STATE_DIR ?= state
SVN_PUBLISH_URL ?=
SVN_TOKEN ?= dummy

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
	for fn in atr/docs/*.md; do out=$${fn#atr/}; uv run --frozen python3 scripts/gfm_to_html.py "$$fn" "$${out%.md}.html"; done
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

check-clean:
	uv run --frozen pre-commit clean

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

docs:
	mkdir -p docs
	uv run --frozen python3 scripts/docs_check.py
	rm -f docs/*.html
	uv run --frozen python3 scripts/docs_build.py
	for fn in atr/docs/*.md; do out=$${fn#atr/}; uv run --frozen python3 scripts/gfm_to_html.py "$$fn" "$${out%.md}.html"; done
	uv run --frozen python3 scripts/docs_post_process.py docs/*.html
	uv run --frozen python3 scripts/docs_check.py

e2e:
	sh tests/run-e2e.sh

e2e-clean:
	cd tests && docker compose down -v && docker compose build --no-cache atr-dev && cd ..

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
	  -e APP_HOST=localhost.apache.org:8080 -e TESTS=1 \
	  -e SSH_HOST=0.0.0.0 -e BIND=0.0.0.0:8080 \
	  tooling-trusted-release

run-playwright:
	docker run --net=host -it atr-playwright python3 test.py --skip-slow

run-playwright-slow:
	docker run --net=host -it atr-playwright python3 test.py --tidy

serve:
	@STATE_DIR="$(STATE_DIR)" scripts/check-certs
	@STATE_DIR="$(STATE_DIR)" scripts/check-perms
	@root="$$PWD"; case "$(STATE_DIR)" in /*) sd="$(STATE_DIR)";; *) sd="$$root/$(STATE_DIR)";; esac; \
	mkdir -p "$$sd/launch" && cd "$$sd/launch" && \
	SSH_HOST=127.0.0.1 STATE_DIR="$$sd" PYTHONPATH="$$root" \
	  uv run --frozen --project "$$root" hypercorn --bind $(BIND) \
	  --keyfile "$$sd/hypercorn/secrets/localhost.apache.org+2-key.pem" \
	  --certfile "$$sd/hypercorn/secrets/localhost.apache.org+2.pem" \
	  atr.server:app --debug --reload --worker-class uvloop

serve-local: svn-dev-repo
	@STATE_DIR="$(STATE_DIR)" scripts/check-certs
	@STATE_DIR="$(STATE_DIR)" scripts/check-perms
	@root="$$PWD"; case "$(STATE_DIR)" in /*) sd="$(STATE_DIR)";; *) sd="$$root/$(STATE_DIR)";; esac; \
	svnurl="$(SVN_PUBLISH_URL)"; svnurl="$${svnurl:-file://$$sd/dev-svn-repo}"; \
	mkdir -p "$$sd/launch" && cd "$$sd/launch" && \
	APP_HOST=localhost.apache.org:8080 DISABLE_CHECK_CACHE=1 TESTS=1 \
	  SVN_PUBLISH_URL="$$svnurl" SVN_TOKEN="$(SVN_TOKEN)" \
	  SSH_HOST=127.0.0.1 STATE_DIR="$$sd" PYTHONPATH="$$root" \
	  uv run --frozen --project "$$root" hypercorn --bind $(BIND) \
	  --keyfile "$$sd/hypercorn/secrets/localhost.apache.org+2-key.pem" \
	  --certfile "$$sd/hypercorn/secrets/localhost.apache.org+2.pem" \
	  atr.server:app --debug --reload --worker-class uvloop

svn-dev-repo:
	@if test ! -d $(STATE_DIR)/dev-svn-repo/db; \
	then svnadmin create $(STATE_DIR)/dev-svn-repo && echo "Created local SVN repo at $(STATE_DIR)/dev-svn-repo"; \
	else echo "Local SVN repo already exists at $(STATE_DIR)/dev-svn-repo"; \
	fi
	@echo
	@case "$(STATE_DIR)" in /*) sd="$(STATE_DIR)";; *) sd="$$PWD/$(STATE_DIR)";; esac; \
	svnurl="$(SVN_PUBLISH_URL)"; echo "serve-local publishes to $${svnurl:-file://$$sd/dev-svn-repo} unless overridden"

sync:
	uv sync --frozen --no-dev
	python3 -S scripts/check_pth_files.py

sync-all:
	uv sync --frozen --all-groups
	python3 -S scripts/check_pth_files.py

unit:
	sh tests/run-unit.sh

update-deps:
	pre-commit autoupdate || :
	uv lock --upgrade
	uv sync --frozen --all-groups
	python3 -S scripts/check_pth_files.py
	uv export --frozen --format requirements-txt --no-emit-project --no-header --no-hashes > pip-audit.requirements
