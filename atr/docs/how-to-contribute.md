# 3.10. How to contribute

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.9.` [Code conventions](code-conventions)

**Next**: (none)

**Sections**:

* [Quick start](#quick-start)
* [Commit message style](#commit-message-style)
* [ASF contribution policies](#asf-contribution-policies)
* [Special considerations for ATR](#special-considerations-for-atr)
* [Getting help](#getting-help)

## Quick start

For the contribution workflow, see **[CONTRIBUTING.md](https://github.com/apache/tooling-trusted-releases/blob/main/CONTRIBUTING.md)** in the repository root.

That guide covers:

- Development setup
- Pull request workflow
- Running tests
- Code standards summary

**IMPORTANT:** New contributors must introduce themselves on [the development mailing list](mailto:dev@tooling.apache.org) first, to deter spam. Please do not submit a PR until you have introduced yourself.

The rest of this page covers detailed policies and guidelines.

## Commit message style

We follow a consistent style for commit messages. The first line (subject line) should:

* **Use the imperative mood.** Complete the sentence "If applied, this commit will..."
* **Use sentence case.** Start with a capital letter, no full stop at the end.
* **Use articles before nouns.** Write "Fix a bug", not "Fix bug".
* **Be specific and descriptive.** Prefer "Fix a bug in vote resolution for tied votes" to "Fix a bug".
* **Keep it concise.** Aim for 50-72 characters.

**Good examples:**

```
Add distribution platform validation to the compose phase
Fix a bug with sorting version numbers containing release candidates
Move code to delete releases to the storage interface
Update dependencies
```

**Poor examples:**

```
fixed stuff
Updated the code.
refactoring vote resolution logic
```

Most commits do not need a body. For complex changes, add a body separated by a blank line explaining _what_ and _why_ (not how). We typically use asterisk-itemized lists.

## ASF contribution policies

As an Apache Software Foundation project, ATR follows standard ASF contribution and licensing policies.

### Contributor License Agreement

Before your first contribution, you must sign the [Apache Individual Contributor License Agreement](https://www.apache.org/licenses/contributor-agreements.html#clas) (ICLA). This is a one-time requirement.

The ICLA grants the ASF the right to distribute and build upon your work, while you retain full rights to use your contributions for any other purpose. It is not a copyright assignment. See the [ASF new committers guide](https://infra.apache.org/new-committers-guide.html#submitting-your-individual-contributor-license-agreement-icla) for submission instructions.

If your employer holds rights to your work, you may also need a [Corporate Contributor License Agreement](https://www.apache.org/licenses/contributor-agreements.html#clas) (CCLA). Consult your employer to determine if this is necessary.

### Licensing

All contributions are licensed under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0). By submitting a pull request, you agree to this license.

Third-party code or dependencies must be compatible with Apache License 2.0:

* [Category A licenses](https://www.apache.org/legal/resolved.html#category-a) - Compatible
* [Category X licenses](https://www.apache.org/legal/resolved.html#category-x) - Not compatible

### Code of conduct

All contributors must follow the [ASF Code of Conduct](https://www.apache.org/foundation/policies/conduct.html).

## Special considerations for ATR

ATR is developed by ASF Tooling, an ASF initiative rather than a top-level project (TLP). This affects governance and development processes. More significantly, ATR has stringent security requirements.

### Security focus

The primary goal of ATR is to deter and minimize supply chain attacks on ASF software releases. We scrutinize all contributions for potential vulnerabilities.

When contributing:

* **Follow secure coding practices.** Avoid injection attacks, cross-site scripting, insecure deserialization.
* **Validate all inputs and sanitize all outputs.**
* **Use established libraries** for cryptographic or security-sensitive functionality. Prefer well-established, independently audited, actively maintained libraries.
* **Consider security implications.** If unsure, ask the team for guidance.
* **Report vulnerabilities responsibly.** Do not open public issues for security problems. Follow the [ASF security reporting process](https://www.apache.org/security/).

### High quality standards

Because of ATR's critical nature, we maintain very high code quality standards. The review process may take longer than expected, and we may request extensive changes. Our goal is to keep ATR as secure and reliable as possible.

### Access controls

We strongly encourage all contributors to enable two-factor authentication on their GitHub accounts, preferably with a [passkey](https://en.wikipedia.org/wiki/WebAuthn#Passkey_branding).

## Getting help

* **Mailing list:** [dev@tooling.apache.org](https://lists.apache.org/list.html?dev@tooling.apache.org) - Primary forum for development discussions
* **Issue tracker:** [GitHub Issues](https://github.com/apache/tooling-trusted-releases/issues) - Comment on issues or PRs
* **Slack:** [#apache-trusted-releases](https://the-asf.slack.com/archives/C049WADAAQG) on ASF Slack
* **Documentation:** The rest of the [Developer Guide](developer-guide)

### Alternative: email patches

If you prefer not to use GitHub, you can [email patches](https://lists.apache.org/list.html?dev@tooling.apache.org) using standard Git patch formatting.
