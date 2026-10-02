# 7. Developer guide

**Up**: [Documentation](.)

**Prev**: `6.` [Release catalog](release-catalog)

**Next**: `7.1.` [Overview of the code](overview-of-the-code)

**Pages**:

* `7.1.` [Overview of the code](overview-of-the-code)
* `7.2.` [How to contribute](how-to-contribute)
* `7.3.` [Code conventions](code-conventions)
* `7.4.` [Code policies](code-policies)
* `7.5.` [Build processes](build-processes)
* `7.6.` [Running the server](running-the-server)
* `7.7.` [Running and creating tests](running-and-creating-tests)
* `7.8.` [Database](database)
* `7.9.` [Storage interface](storage-interface)
* `7.10.` [User interface](user-interface)
* `7.11.` [Tasks](tasks)
* `7.12.` [Authentication security](authentication-security)
* `7.13.` [Sessions](sessions)
* `7.14.` [Authorization security](authorization-security)
* `7.15.` [Input validation](input-validation)
* `7.16.` [Dependency updates](dependency-updates)
* `7.17.` [TLS security configuration](tls-security-configuration)
* `7.18.` [API documentation policy](api-documentation-policy)
* `7.19.` [ASF modules](asf-modules)
* `7.20.` [Resource management](resource-management)
* `7.21.` [SBOM architecture](sbom-architecture)
* `7.22.` [File handling](file-handling)

**Sections**:

* [Introduction](#introduction)
* [Security documentation](#security-documentation)

## Introduction

This is a guide for developers of ATR, explaining how to make changes to the ATR source code. For more information about how to contribute those changes back to us, please read the [contribution guide](how-to-contribute).

## Security documentation

ATR is security-critical infrastructure for the Apache Software Foundation. Before contributing, you should familiarize yourself with our security practices:

* [Authentication security](authentication-security) - How users authenticate to ATR via ASF OAuth and API tokens
* [Authorization security](authorization-security) - The role-based access control model and LDAP integration
* [Input validation](input-validation) - Data validation patterns and injection prevention
* [File handling](file-handling) - Upload limits, archive validation and downloads

For reporting security vulnerabilities, see [SECURITY.md](https://github.com/apache/tooling-trusted-releases/blob/main/SECURITY.md) in the repository root.
