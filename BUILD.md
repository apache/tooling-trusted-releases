# Build Guide

This guide covers building ATR and its components. For development setup, see [DEVELOPMENT.md](DEVELOPMENT.md).

## Prerequisites

- **Docker or Podman** - For container builds
- **uv** - Python package manager
- **make** - POSIX-compliant make utility
- **cmark** - CommonMark processor (for documentation)
- **Python 3.13** - Required runtime

Install on Alpine Linux:
```shell
apk add cmark curl git make mkcert@testing
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/usr/local/bin" sh
uv python install 3.13
```

Install on macOS (Homebrew):
```shell
brew install cmark mkcert
curl -LsSf https://astral.sh/uv/install.sh | sh
rehash
uv python install 3.13
```

## Container Build

### Build the Alpine Container

```shell
make build-alpine
# or simply
make build
```

This runs `scripts/build` to create the `tooling-trusted-release` container image using `Dockerfile.alpine`.

### Run the Container

```shell
make certs-local  # Generate certificates first
make run-alpine
```

### Docker Compose

For development with auto-reload:

```shell
mkdir -p state
docker compose up --build
```

The compose configuration:
- Mounts `atr/` for live code changes
- Enables test mode (`ALLOW_TESTS=1`)
- Exposes port 8080

## Documentation Build

### Build All Documentation

```shell
make docs
```

This command:
1. Validates the table of contents structure
2. Generates navigation links between pages
3. Converts Markdown to HTML using cmark
4. Post-processes HTML files

### Build Without Validation

```shell
make build-docs
```

### How Documentation Build Works

The documentation system uses `scripts/docs_build.py` to automatically generate navigation from the table of contents in `atr/docs/index.md`. When you reorganize documentation, just edit the table of contents and run `make docs` to update all navigation links.

For details, see [Build Processes](https://release-test.apache.org/docs/build-processes).

## Python Dependencies

### Install All Dependencies

```shell
uv sync --frozen --all-groups
```

### Install Production Dependencies Only

```shell
uv sync --frozen --no-dev
```

### Update Dependencies

```shell
make update-deps
```

This updates `uv.lock` and runs `pre-commit autoupdate`.

## TLS Certificates

### For Local Development (mkcert)

```shell
make certs-local
```

Creates certificates in `state/hypercorn/secrets/` using mkcert.

### Self-Signed Certificates

```shell
make certs
```

Generates self-signed certificates using `scripts/generate-certificates`.

## Frontend Assets

### Build Bootstrap

```shell
make build-bootstrap
```

### Bump Bootstrap Version

```shell
make bump-bootstrap BOOTSTRAP_VERSION=5.3.4
```

### Build TypeScript

```shell
make build-ts
```

## Test Builds

### Build Playwright Container

```shell
make build-playwright
```

### Run Playwright Tests

```shell
make run-playwright       # Fast tests
make run-playwright-slow  # All tests with cleanup
```

Or use Docker Compose:

```shell
sh tests/run-playwright.sh
```

### Run End-to-End Tests

```shell
sh tests/run-e2e.sh
```

## Make Targets Reference

### Build Targets

| Target | Description |
|--------|-------------|
| `build` | Alias for `build-alpine` |
| `build-alpine` | Build the Alpine-based container |
| `build-bootstrap` | Build Bootstrap assets |
| `build-docs` | Build documentation without validation |
| `build-playwright` | Build Playwright test container |
| `build-ts` | Compile TypeScript |

### Run Targets

| Target | Description |
|--------|-------------|
| `serve` | Run server with standard config |
| `serve-local` | Run server with debug and test mode |
| `run-alpine` | Run the Alpine container |
| `run-playwright` | Run Playwright tests (fast) |
| `run-playwright-slow` | Run Playwright tests (full) |

### Dependency Targets

| Target | Description |
|--------|-------------|
| `sync` | Install production dependencies |
| `sync-all` | Install all dependencies including dev |
| `update-deps` | Update and lock dependencies |

### Code Quality Targets

| Target | Description |
|--------|-------------|
| `check` | Run all pre-commit checks |
| `check-light` | Run lightweight checks |
| `check-heavy` | Run comprehensive checks |
| `check-extra` | Run interface ordering checks |

### Utility Targets

| Target | Description |
|--------|-------------|
| `certs` | Generate self-signed certificates |
| `certs-local` | Generate mkcert certificates |
| `docs` | Build and validate documentation |
| `generate-version` | Generate version.py |
| `commit` | Add, commit, pull, push workflow |
| `ipython` | Start IPython shell with project |

## Configuration Variables

The Makefile supports these variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `BIND` | `127.0.0.1:8080` | Server bind address |
| `IMAGE` | `tooling-trusted-release` | Container image name |
| `STATE_DIR` | `state` | State directory path |

Example:
```shell
make serve-local BIND=0.0.0.0:8080
```

## CI/CD

The GitHub Actions workflow (`.github/workflows/build.yml`) runs:
1. Pre-commit checks
2. Playwright browser tests
3. Container build verification

See the workflow file for details on the CI environment.