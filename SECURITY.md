# Security Policy

## Reporting security issues

**Do NOT report security vulnerabilities through public GitHub issues.**

Please report security vulnerabilities to the Apache Security Team by emailing **security@apache.org**. This is a private mailing list. Please send one plain-text, unencrypted, email for each vulnerability you are reporting.

The ASF Security Team coordinates the handling of all security vulnerabilities for Apache projects. The vulnerability handling process is:

1. The reporter reports the vulnerability privately to Apache
2. The appropriate project's security team works privately with the reporter to resolve the vulnerability
3. The project creates a new release of the package the vulnerability affects to deliver its fix
4. The project publicly announces the vulnerability and describes how to apply the fix

For complete details on reporting and the process, see the [ASF Security Team](https://www.apache.org/security/) page.

## Scope

This security policy applies to:

* The ATR application (including web interface and API)
* Documentation and examples in this repository

Out of scope:

* Third-party dependencies (report to the respective project)
* ASF infrastructure not specific to ATR (report to ASF Infrastructure at root@apache.org)

## Recognition

We are grateful to security researchers who help us improve ATR. With your permission, we will acknowledge your contribution in release notes.

## Security resources

For more information about ATR security:

* [Authentication documentation](https://release-test.apache.org/docs/security-authentication) - How users authenticate to ATR
* [Authorization documentation](https://release-test.apache.org/docs/security-authorization) - Access control model
* [Input validation documentation](https://release-test.apache.org/docs/input-validation) - Data validation patterns

## Supported versions

ATR is a continuously deployed service. We address security issues in the current production version. There are no separately maintained release branches at this time.
