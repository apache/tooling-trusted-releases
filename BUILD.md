# Build guide

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

## Container build

### Build the Alpine container

```shell
make build-alpine
# or simply
make build
```

This runs `scripts/build` to create the `tooling-trusted-release` container image using `Dockerfile.alpine`.

### Run the container

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
- Enables test mode (`TESTS=1`)
- Exposes port 8080

## Documentation build

### Build all documentation

```shell
make docs
```

This command:

1. Validates the table of contents structure
2. Generates navigation links between pages
3. Converts Markdown to HTML using cmark
4. Post-processes HTML files

### Build without validation

```shell
make build-docs
```

### How documentation build works

The documentation system uses `scripts/docs_build.py` to automatically generate navigation from the table of contents in `atr/docs/index.md`. When you reorganize documentation, just edit the table of contents and run `make docs` to update all navigation links.

For details, see [Build Processes](https://release-test.apache.org/docs/build-processes).

## Python dependencies

### Install all dependencies

```shell
uv sync --frozen --all-groups
```

### Install production dependencies only

```shell
uv sync --frozen --no-dev
```

### Update dependencies

```shell
make update-deps
```

This updates `uv.lock` and runs `pre-commit autoupdate`.

## TLS certificates

### For local development (mkcert)

```shell
make certs-local
```

Creates certificates in `state/hypercorn/secrets/` using mkcert.

### Self-signed certificates

```shell
make certs
```

Generates self-signed certificates using `scripts/generate-certificates`.

## Frontend assets

### Build Bootstrap

```shell
make build-bootstrap
```

### Bump Bootstrap version

```shell
make bump-bootstrap BOOTSTRAP_VERSION=5.3.4
```

### Build TypeScript

```shell
make build-ts
```

## Test builds

### Build Playwright container

```shell
make build-playwright
```

### Run Playwright tests

```shell
make run-playwright       # Fast tests
make run-playwright-slow  # All tests with cleanup
```

Or use Docker Compose:

```shell
sh tests/run-playwright.sh
```

### Run end-to-end tests

```shell
sh tests/run-e2e.sh
```

## Make targets reference

### Build targets

| Target             | Description                            |
| ------------------ | -------------------------------------- |
| `build`            | Alias for `build-alpine`               |
| `build-alpine`     | Build the Alpine-based container       |
| `build-bootstrap`  | Build Bootstrap assets                 |
| `build-docs`       | Build documentation without validation |
| `build-playwright` | Build Playwright test container        |
| `build-ts`         | Compile TypeScript                     |

### Run targets

| Target                  | Description                         |
| ----------------------- | ----------------------------------- |
| `serve`                 | Run server with standard config     |
| `serve-local`           | Run server with debug and test mode |
| `run-alpine`            | Run the Alpine container            |
| `run-playwright`        | Run Playwright tests (fast)         |
| `run-playwright-slow`   | Run Playwright tests (full)         |

### Dependency targets

| Target        | Description                            |
| ------------- | -------------------------------------- |
| `sync`        | Install production dependencies        |
| `sync-all`    | Install all dependencies including dev |
| `update-deps` | Update and lock dependencies           |

### Code quality targets

| Target         | Description                    |
| -------------- | ------------------------------ |
| `check`        | Run all pre-commit checks      |
| `check-light`  | Run lightweight checks         |
| `check-heavy`  | Run comprehensive checks       |
| `check-extra`  | Run interface ordering checks  |

### Utility targets

| Target             | Description                        |
| ------------------ | ---------------------------------- |
| `certs`            | Generate self-signed certificates  |
| `certs-local`      | Generate mkcert certificates       |
| `docs`             | Build and validate documentation   |
| `generate-version` | Generate version.py                |
| `commit`           | Add, commit, pull, push workflow   |
| `ipython`          | Start IPython shell with project   |

## Configuration variables

The Makefile supports these variables:

| Variable    | Default                   | Description           |
| ----------- | ------------------------- | --------------------- |
| `BIND`      | `127.0.0.1:8080`          | Server bind address   |
| `IMAGE`     | `tooling-trusted-release` | Container image name  |
| `STATE_DIR` | `state`                   | State directory path  |

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
