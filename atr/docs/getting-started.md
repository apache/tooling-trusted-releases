# 1.1. Getting started

**Up**: `1.` [Introduction to ATR](introduction-to-atr)

**Prev**: (none)

**Next**: `1.2.` [Project configuration](project-configuration)

**Sections**:

* [Getting started with ATR](#getting-started-with-atr)

## Getting started with ATR

There are prepatory steps you need to consider prior to starting your first ATR release as a Release Manager.
If your PMC is new to using ATR you will want to make sure that the project you are releasing a new version for is properly configured.
In ATR you move your release candidate's artifacts into the system using one or more of several methods while traditionally you would commit
them to `svn:dist:dev`.

1. ATR uses the ASF's MFA at mfa.apache.org. Make sure that you are registered first.
2. If you are a Release Manager who is not a PMC member then you will need a PMC member to "designate" you as one from the Committee page.
   This is a change from legacy policy.
3. You need to make sure that ATR has your [OpenPGP public key](/keys) is linked to your PMC. If your key is already in your project's
   `KEYS` file on `svn:dist:release` then you are ready. If not then ATR allows you to manage your [OpenPGP public keys](/keys).
4. Depending on how you choose to upload your release candidate artifacts you may need to create a [Parsonal Access Token](/tokens) or
   upload a [public `SSH` key](/keys).
5. Check your PMC's [release catalog](https://release-catalog.apache.org) to assure that the list of projects is correct. Reach out to the
   tooling team to work on any corrections.
6. When you login to ATR your [home page](/) shows you a list of your projects as an index and scroll down to
   * Check the project's configuration through the "About this project" link.
   * See any release candidates that are in process.
   * Start a new release candidate.
   * See any finished releases including current and archived made through ATR and also as found on https://downloads.apache.org/ and
     https://archive.apache.org/
