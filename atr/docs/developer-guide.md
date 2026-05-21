# 3. Developer guide

**Up**: [Documentation](.)

**Prev**: `2.` [User guide](user-guide)

**Next**: (none)

**Pages**:

* `3.1.` [Running the server](running-the-server)
* `3.2.` [Overview of the code](overview-of-the-code)
* `3.3.` [Database](database)
* `3.4.` [Storage interface](storage-interface)
* `3.5.` [User interface](user-interface)
* `3.6.` [Tasks](tasks)
* `3.7.` [Build processes](build-processes)
* `3.8.` [Running and creating tests](running-and-creating-tests)
* `3.9.` [Code conventions](code-conventions)
* `3.10.` [Code policies](code-policies)
* `3.11.` [How to contribute](how-to-contribute)
* `3.12.` [Authentication security](authentication-security)
* `3.13.` [Authorization security](authorization-security)
* `3.14.` [Input validation](input-validation)
* `3.15.` [TLS security configuration](tls-security-configuration)
* `3.16.` [API documentation policy](api-documentation-policy)
* `3.17.` [ASFQuart usage](asfquart-usage)

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

For reporting security vulnerabilities, see [SECURITY.md](https://github.com/apache/tooling-trusted-releases/blob/main/SECURITY.md) in the repository root.
