# Contributing to ATR

Thank you for your interest in contributing to Apache Trusted Releases (ATR)! This guide will help you get started.

For detailed ASF policies, commit message guidelines, and security considerations, see the [contribution policies guide](https://release-test.apache.org/docs/how-to-contribute).

## Before You Start

> **IMPORTANT:** New contributors must introduce themselves on the [development mailing list](mailto:dev@tooling.apache.org) first, to deter spam. Please do not submit a PR until you have introduced yourself, otherwise it will likely be rejected.

**Subscribe to the mailing list:** Send an email with empty subject and body to [dev-subscribe@tooling.apache.org](mailto:dev-subscribe@tooling.apache.org) and reply to the automated response.

## Finding Something to Work On

- Browse the [issue tracker](https://github.com/apache/tooling-trusted-releases/issues) for open issues
- For new features or bugs, [create an issue](https://github.com/apache/tooling-trusted-releases/issues/new) to discuss before starting work

## Development Setup

1. **Fork and clone** the repository:
   ```shell
   git clone https://github.com/YOUR_USERNAME/tooling-trusted-releases.git
   cd tooling-trusted-releases
   ```

2. **Install dependencies** (includes pre-commit, dev tools, and test dependencies):
   ```shell
   # Install uv if you don't have it
   curl -LsSf https://astral.sh/uv/install.sh | sh
   
   # Install all dependencies
   uv sync --frozen --all-groups
   ```

3. **Set up pre-commit hooks:**
   ```shell
   uv run pre-commit install
   ```

4. **Run the server:** See [DEVELOPMENT.md](DEVELOPMENT.md) for detailed instructions.

## Pull Request Workflow

1. **Create a branch** with a descriptive name:
   ```shell
   git checkout -b fix-typo-in-docs
   ```

2. **Make your changes** following our [code conventions](https://release-test.apache.org/docs/code-conventions)

3. **Run checks** before committing:
   ```shell
   make check
   ```

4. **Commit** with a clear message (see [commit style](#commit-message-style) below)

5. **Push** your branch:
   ```shell
   git push origin your-branch-name
   ```

6. **Open a pull request** to the `main` branch
   - Explain what your changes do and why
   - Reference any related issues (e.g., "Fixes #123")
   - Mark as draft until ready for review
   - **Enable "Allow maintainer edits"** (strongly recommended)

7. **Participate in review** - we may request changes

## Commit Message Style

Use clear, concise commit messages:

**Format:**
- First line: imperative mood, sentence case, 50-72 characters
- No period at the end
- Use articles ("Fix a bug" not "Fix bug")

**Good examples:**
```
Add distribution platform validation to the compose phase
Fix a bug with sorting version numbers containing release candidates
Update dependencies
```

**Poor examples:**
```
fixed stuff
Updated the code.
refactoring vote resolution logic
```

For complex changes, add a body separated by a blank line explaining what and why (not how).

## Code Standards Summary

- **Python:** Follow PEP 8, use double quotes, no `# noqa` or `# type: ignore`
- **HTML:** Use Bootstrap classes, avoid custom CSS
- **JavaScript:** Minimize usage, follow best practices for dependencies
- **Shell:** POSIX sh only, no bash-specific features

See the [full code conventions](https://release-test.apache.org/docs/code-conventions) for complete guidelines.

## Running Tests

```shell
# Browser tests (requires Docker)
sh tests/run-playwright.sh

# End-to-end tests
sh tests/run-e2e.sh

# Quick pre-commit checks
make check-light
```

## ASF Requirements

### Contributor License Agreement

Before your first contribution, sign the [Apache ICLA](https://www.apache.org/licenses/contributor-agreements.html#clas). This is a one-time requirement.

If your employer holds rights to your work, a [CCLA](https://www.apache.org/licenses/contributor-agreements.html#clas) may also be needed.

### Licensing

All contributions are licensed under [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0). Third-party dependencies must be compatible ([Category A licenses](https://www.apache.org/legal/resolved.html#category-a)).

### Code of Conduct

Follow the [ASF Code of Conduct](https://www.apache.org/foundation/policies/conduct.html).

## Security Considerations

ATR's primary goal is to prevent supply chain attacks. When contributing:

- Follow secure coding practices
- Validate all inputs and sanitize outputs
- Use established libraries for cryptographic operations
- Consider security implications of your changes
- Report security issues via the [ASF security process](https://www.apache.org/security/) (not public issues)

## Getting Help

- **Mailing list:** [dev@tooling.apache.org](https://lists.apache.org/list.html?dev@tooling.apache.org)
- **Slack:** [#apache-trusted-releases](https://the-asf.slack.com/archives/C049WADAAQG) on ASF Slack
- **Issue tracker:** Comment on relevant issues or PRs
- **Documentation:** [Developer Guide](https://release-test.apache.org/docs/developer-guide)

## Alternative: Email Patches

If you prefer not to use GitHub, you can [email patches](https://lists.apache.org/list.html?dev@tooling.apache.org) using standard Git patch formatting.