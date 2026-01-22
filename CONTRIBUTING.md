# Contributing to ATR

Thank you for your interest in contributing to Apache Trusted Releases (ATR)! This guide will help you get started.

For detailed ASF policies, commit message guidelines, and security considerations, see the [contribution policies guide](https://release-test.apache.org/docs/how-to-contribute).

## Before you start

> **IMPORTANT:** New contributors must introduce themselves on the [development mailing list](mailto:dev@tooling.apache.org) first, to deter spam. Please do not submit a PR until you have introduced yourself, otherwise it will likely be rejected.

**Subscribe to the mailing list:** Send an email with empty subject and body to [dev-subscribe@tooling.apache.org](mailto:dev-subscribe@tooling.apache.org) and reply to the automated response.

## Finding something to work on

- Browse the [issue tracker](https://github.com/apache/tooling-trusted-releases/issues) for open issues
- For new features or bugs, [create an issue](https://github.com/apache/tooling-trusted-releases/issues/new) to discuss before starting work

## Development setup

1. **Fork and clone** the repository:

   ```shell
   git clone https://github.com/YOUR_USERNAME/tooling-trusted-releases.git
   cd tooling-trusted-releases
   git remote add upstream https://github.com/apache/tooling-trusted-releases.git
   git config pull.rebase true
   ```

   This configures `origin` to point to your fork and `upstream` to point to the Apache repository. Setting `pull.rebase true` keeps your commit history clean by rebasing rather than creating merge commits.

   **Important:** Never commit directly to your fork's `main` branch. Always create feature branches for your work. This keeps your `main` in sync with upstream and avoids conflicts.

   Before starting new work, sync your fork with upstream:

   ```shell
   git checkout main
   git pull upstream main
   git push origin main
   ```

   The `git push origin main` updates your fork on GitHub. Do this regularly to keep your fork current.

2. **Install dependencies** (includes pre-commit, dev tools, and test dependencies):

   ```shell
   # Install uv if you don't have it
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # Install all dependencies
   uv sync --frozen --all-groups
   ```

3. **Set up pre-commit hooks:**

   ```shell
   uv run pre-commit --frozen install
   ```

4. **Run the server:** See [DEVELOPMENT.md](DEVELOPMENT.md) for detailed instructions.

## Pull request workflow

1. **Create a branch** with a descriptive name:

   ```shell
   git checkout -b fix-typo-in-docs
   ```

2. **Make your changes** following our [code conventions](https://release-test.apache.org/docs/code-conventions)

3. **Run checks and tests** before committing:

   ```shell
   make check              # Required: lints and type checks
   sh tests/run-e2e.sh     # Required: end-to-end tests
   sh tests/run-unit.sh    # Required: unit tests
   ```

   All checks and tests must pass locally before submitting.

4. **Commit** with a clear message (see [commit style](#commit-message-style) below)

5. **Rebase on main** before pushing:

   ```shell
   git fetch upstream
   git rebase upstream/main
   ```

   If you have conflicts, resolve them in each file, then `git add` the resolved files and run `git rebase --continue`. If you get stuck, `git rebase --abort` returns to your previous state.

6. **Push** your branch:

   ```shell
   git push origin your-branch-name
   ```

   If you've rebased a branch that was previously pushed, you'll need to force push:

   ```shell
   git push --force-with-lease origin your-branch-name
   ```

7. **Open a pull request** to the `main` branch
   - Fill out the PR template completely, confirming all required acknowledgements
   - Reference any related issues (e.g., "Fixes #123")
   - **Open as Draft** until all checks pass and you have confirmed local testing
   - **Enable "Allow maintainer edits"** (strongly recommended)
   - Convert from Draft to ready for review only after all acknowledgements are confirmed

8. **Participate in review** - we may request changes

PRs that fail to demonstrate proper local testing or do not complete the PR template may be closed.

## Commit message style

Use clear, concise commit messages:

**Format:**

- First line: imperative mood, sentence case, 50-72 characters
- No period at the end
- Use articles ("Fix a bug" not "Fix bug")

**Good examples:**

```text
Add distribution platform validation to the compose phase
Fix a bug with sorting version numbers containing release candidates
Update dependencies
```

**Poor examples:**

```text
fixed stuff
Updated the code.
refactoring vote resolution logic
```

For complex changes, add a body separated by a blank line explaining what and why (not how).

## Code standards summary

- **Python:** Follow PEP 8, use double quotes, no `# noqa` or `# type: ignore`
- **HTML:** Use Bootstrap classes, avoid custom CSS
- **JavaScript:** Minimize usage, follow best practices for dependencies
- **Shell:** POSIX sh only, no bash-specific features

See the [full code conventions](https://release-test.apache.org/docs/code-conventions) for complete guidelines.

## Running tests

```shell
# Full pre-commit checks (required before submitting PR)
make check

# End-to-end tests (required before submitting PR)
sh tests/run-e2e.sh

# Unit tests (required before submitting PR)
sh tests/run-unit.sh

# Browser tests (requires Docker)
sh tests/run-playwright.sh

# Quick pre-commit checks (for rapid iteration)
make check-light
```

## ASF requirements

### Contributor License Agreement

Before your first contribution, sign the [Apache ICLA](https://www.apache.org/licenses/contributor-agreements.html#clas). This is a one-time requirement.

If your employer holds rights to your work, a [CCLA](https://www.apache.org/licenses/contributor-agreements.html#clas) may also be needed.

### Licensing

All contributions are licensed under [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0). Third-party dependencies must be compatible ([Category A licenses](https://www.apache.org/legal/resolved.html#category-a)).

### Code of Conduct

Follow the [ASF Code of Conduct](https://www.apache.org/foundation/policies/conduct.html).

## Security considerations

ATR's primary goal is to prevent supply chain attacks. When contributing:

- Follow secure coding practices
- Validate all inputs and sanitize outputs
- Use established libraries for cryptographic operations
- Consider security implications of your changes
- Report security issues via the [ASF security process](https://www.apache.org/security/) (not public issues)

## Getting help

- **Mailing list:** [dev@tooling.apache.org](https://lists.apache.org/list.html?dev@tooling.apache.org)
- **Slack:** [#apache-trusted-releases](https://the-asf.slack.com/archives/C049WADAAQG) on ASF Slack
- **Issue tracker:** Comment on relevant issues or PRs
- **Documentation:** [Developer Guide](https://release-test.apache.org/docs/developer-guide)

## Alternative: email patches

If you prefer not to use GitHub, you can [email patches](https://lists.apache.org/list.html?dev@tooling.apache.org) using standard Git patch formatting.
