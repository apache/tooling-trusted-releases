# Apache Trusted Releases (ATR)

*A prototype service for verifying and distributing Apache releases securely.*

<a href="https://github.com/apache/tooling-trusted-releases/actions/workflows/build.yml?query=branch%3Amain">
  <img alt="Build & Tests" src="https://github.com/apache/tooling-trusted-releases/actions/workflows/build.yml/badge.svg?branch=main" /></a>
<a href="https://github.com/apache/tooling-trusted-releases/actions/workflows/analyze.yml">
  <img alt="Analyze using pre-commit hooks" src="https://github.com/apache/tooling-trusted-releases/actions/workflows/analyze.yml/badge.svg" /></a>
<a href="https://github.com/apache/tooling-trusted-releases/blob/main/LICENSE">
  <img alt="Apache License" src="https://img.shields.io/github/license/apache/tooling-trusted-releases" /></a>

> **NOTE:** New contributors must introduce themselves on [the development mailing list](mailto:dev@tooling.apache.org) first, to deter spam. Contributions are very welcome, but please do not submit a PR until you have introduced yourself.

## Status

This repository contains code developed by the **Apache Software Foundation (ASF) Tooling team**.

As of **January 2026**, this code is available for **internal ASF feedback only**.
The project is in **alpha development** and subject to significant changes.

We welcome feedback and discussion, but note that many known issues and design refinements are already scheduled for future iterations.
Please review our [issue tracker](https://github.com/apache/tooling-trusted-releases/issues) and inline comments before filing new issues.

**Alpha test deployment:** 🔗 https://release-test.apache.org/

> **Note:** This repository is not yet an officially maintained or endorsed ASF project.
> It does not represent final technical or policy decisions for future ASF Tooling products.
> The code is provided without guarantees regarding stability, security, or backward compatibility.

## Quick Start

**Run with Docker Compose (recommended):**

```shell
git clone https://github.com/apache/tooling-trusted-releases.git
cd tooling-trusted-releases
mkdir -p state
docker compose up --build
```

Then visit https://127.0.0.1:8080/ (accept the self-signed certificate).

See [DEVELOPMENT.md](DEVELOPMENT.md) for more options including running without containers.

## Documentation

| Document | Description |
|----------|-------------|
| [DEVELOPMENT.md](DEVELOPMENT.md) | Quick start guide for developers |
| [BUILD.md](BUILD.md) | Build instructions and Make targets |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute code |
| [SUPPORT.md](SUPPORT.md) | Getting help and reporting issues |
| [GOVERNANCE.md](GOVERNANCE.md) | Project governance |

**Online documentation:** https://release-test.apache.org/docs/

## Getting Involved

Community feedback is encouraged! If you are an ASF committer or contributor interested in Trusted Releases:

1. **Try it out** – The [alpha test server](https://release-test.apache.org/) allows you to experiment with the release process.

2. **Introduce yourself** on the development mailing list:
   📧 [dev@tooling.apache.org](mailto:dev@tooling.apache.org)
   
   Subscribe by sending email with empty subject and body to [dev-subscribe@tooling.apache.org](mailto:dev-subscribe@tooling.apache.org) and replying to the automated response (per the [ASF mailing list how-to](https://www.apache.org/foundation/mailinglists)).

3. **Share ideas or file issues:**
   Use the [GitHub Issues](https://github.com/apache/tooling-trusted-releases/issues) page to report bugs, suggest features, or discuss improvements.

4. **Chat with us:**
   💬 [#apache-trusted-releases](https://the-asf.slack.com/archives/C049WADAAQG) on ASF Slack

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed contribution guidelines.

**Key resources for contributors:**

- [Contribution policies](https://release-test.apache.org/docs/how-to-contribute) – ASF policies, commit style, security guidelines
- [Developer guide](https://release-test.apache.org/docs/developer-guide) – Technical documentation
- [Server reference](https://release-test.apache.org/docs/running-the-server) – Architecture and configuration details
- [Running and creating tests](https://release-test.apache.org/docs/running-and-creating-tests) – Testing guide
- [Code conventions](https://release-test.apache.org/docs/code-conventions) – Style guidelines

## License

This project is licensed under the [Apache License, Version 2.0](LICENSE).

---

*Part of the [Apache Tooling Initiative](https://tooling.apache.org/).*
For more information about the ASF, visit https://www.apache.org/.