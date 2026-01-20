# Development Guide

This guide will help you get started with developing ATR. For detailed technical documentation, see the [Developer Guide](https://release-test.apache.org/docs/developer-guide).

## Prerequisites

ATR can be developed on **Linux** or **macOS**. Windows and other platforms are not supported.

**Required (install manually):**

- **Git** - For cloning the repository
- **Python 3.13** - The runtime for ATR (can be installed via uv)
- **uv** - Python package manager ([installation guide](https://docs.astral.sh/uv/#installation))
- **Docker or Podman** - For containerized development (recommended)
- **mkcert** - For local TLS certificates (if running directly)
- **make** - POSIX-compliant make utility
- **biome** - For JavaScript/TypeScript linting ([installation guide](https://biomejs.dev/guides/manual-installation/))
- **cmark** - CommonMark processor (optional, for rebuilding documentation)

**Installed via `uv sync`:** pre-commit, ruff, pyright, playwright, and other dev/test tools (see `pyproject.toml`).

### Platform-Specific Installation

**Alpine Linux:**

```shell
apk add cmark curl git make mkcert@testing
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/usr/local/bin" sh
uv python install 3.13
```

**macOS (Homebrew):**

```shell
brew install cmark mkcert
curl -LsSf https://astral.sh/uv/install.sh | sh
rehash
uv python install 3.13
```

## Quick Start

There are two ways to run the server: in a container (recommended) or directly. **Do not use both methods simultaneously** - they share the same state directory and will conflict.

### Option 1: Docker Compose (Recommended)

The easiest way to run ATR with all dependencies (CycloneDX, syft, Apache RAT for SBOM generation and license checking):

```shell
# Clone your fork
git clone https://github.com/YOUR_USERNAME/tooling-trusted-releases.git
cd tooling-trusted-releases

# Create state directory and start
mkdir -p state
docker compose up --build
```

The first build takes several minutes. Subsequent runs are faster due to caching.

Visit [`https://127.0.0.1:8080/`](https://127.0.0.1:8080/) and accept the self-signed certificate.

The container:
- Runs in test mode (`ALLOW_TESTS=1`) with mock authentication
- Mounts `atr/` for live code changes without rebuilding
- Auto-reloads when files change

**Optional LDAP credentials** (for rsync writes and certain tasks):

```shell
LDAP_BIND_DN=dn LDAP_BIND_PASSWORD=pass docker compose up --build
```

**Useful container commands:**

```shell
docker compose exec atr bash          # Shell in running container
docker compose run --rm atr bash      # Start container with shell (not ATR)
docker compose down                   # Stop the server
```

### Option 2: Running Directly

For faster iteration without containers:

```shell
# Clone your fork
git clone https://github.com/YOUR_USERNAME/tooling-trusted-releases.git
cd tooling-trusted-releases

# Install Python dependencies
uv sync --frozen --all-groups

# Create state directory and certificates
mkdir -p state
make certs-local

# Start the server
make serve-local
```

**Accessing the site:**

We recommend using `localhost.apache.org`, which requires adding to `/etc/hosts`:

```
127.0.0.1 localhost.apache.org
```

Then visit: [`https://localhost.apache.org:8080/`](https://localhost.apache.org:8080/)

Alternatively, use [`https://127.0.0.1:8080/`](https://127.0.0.1:8080/) without modifying hosts.

> **Note:** Pick one host and stick with it - logging in on one host doesn't log you in on another.

**Why TLS is required:** ATR uses the actual ASF OAuth server for login, even in development. This keeps development behavior aligned with production.

**Initial startup:** It takes 1-2 minutes to fetch committee and project information from the ASF website. Until complete, no existing committees/projects will appear.

## Development Workflow

1. **Set up pre-commit hooks:**
   ```shell
   uv run pre-commit install
   ```

2. **Run code checks:**
   ```shell
   make check        # Full checks
   make check-light  # Quick checks
   ```

3. **Run tests:**
   ```shell
   sh tests/run-playwright.sh   # Browser tests with Docker Compose
   sh tests/run-e2e.sh          # End-to-end tests
   ```

4. **Build documentation:**
   ```shell
   make docs
   ```

## Key Make Targets

| Target | Description |
|--------|-------------|
| `make serve-local` | Run server locally with debug mode |
| `make check` | Run all pre-commit checks |
| `make check-light` | Run quick pre-commit checks |
| `make docs` | Build documentation |
| `make build-alpine` | Build the Alpine container |
| `make run-playwright` | Run browser tests |

See [BUILD.md](BUILD.md) for the complete list of build targets.

## Project Structure

```
tooling-trusted-releases/
├── atr/              # Main application source code
│   ├── api/          # API endpoints
│   ├── db/           # Database interfaces
│   ├── docs/         # Documentation (Markdown)
│   ├── get/          # GET route handlers
│   ├── models/       # Data models (SQLModel/Pydantic)
│   ├── post/         # POST route handlers
│   ├── shared/       # Shared route handler code
│   ├── storage/      # Storage interface
│   ├── tasks/        # Background task definitions
│   └── templates/    # Jinja2 templates
├── playwright/       # Browser test scripts
├── scripts/          # Build and utility scripts
├── state/            # Runtime state (created at runtime)
└── tests/            # Test configuration and e2e tests
```

## Useful Resources

- **[Overview of the Code](https://release-test.apache.org/docs/overview-of-the-code)** - High-level architecture
- **[Running and Creating Tests](https://release-test.apache.org/docs/running-and-creating-tests)** - Testing guide
- **[Code Conventions](https://release-test.apache.org/docs/code-conventions)** - Style guidelines
- **[Contributing](CONTRIBUTING.md)** - How to contribute code
- **[Build Guide](BUILD.md)** - Complete build targets reference

## Troubleshooting

### Certificate Issues

If you encounter TLS certificate problems when running directly:

1. Ensure `mkcert` is installed and run `make certs-local`
2. You may need to run `mkcert -install` to trust the local CA
3. See the [mkcert documentation](https://github.com/FiloSottile/mkcert) for platform-specific guidance

> **Security note:** `mkcert -install` creates a CA valid for 10 years for your system, Java, and Firefox. If the private key (`rootCA-key.pem` in the directory shown by `mkcert -CAROOT`) is ever leaked, anyone could create certificates trusted by your system. See the [mkcert caveats](https://github.com/FiloSottile/mkcert#installation).

### Container Issues

If Docker Compose fails:

```shell
# Clean up and rebuild
docker compose down -v
docker compose build --no-cache
docker compose up
```

### Session Caching (Local Development)

Developers without LDAP credentials can cache session information:

1. Visit `/user/cache`
2. Press the "Cache me!" button
3. Restart the server to clear the authorization cache if needed

This writes your session to the ATR state directory, which is consulted instead of LDAP. This feature only works in debug mode (`make serve-local`).