# 3.15. TLS security configuration

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.14.` [Input validation](input-validation)

**Next**: `3.16.` [ASFQuart usage](asfquart-usage)

**Sections**:

* [Overview](#overview)
* [Supported TLS versions](#supported-tls-versions)
* [Elliptic curve selection](#elliptic-curve-selection)
* [Cipher suites (TLS 1.2)](#cipher-suites-tls-12)
* [Cipher ordering](#cipher-ordering)
* [Session security](#session-security)
* [Security properties of this configuration](#security-properties-of-this-configuration)
* [Summary](#summary)

## Overview

This server enforces modern TLS security settings aligned with current best practices. The configuration restricts TLS to strong protocol versions, modern cipher suites, secure elliptic curves, and additional protections such as OCSP stapling and disabled session tickets.

```apache
SSLProtocol         -all +TLSv1.2 +TLSv1.3
SSLProxyProtocol    -all +TLSv1.2 +TLSv1.3
SSLOpenSSLConfCmd   Curves X25519:prime256v1:secp384r1

SSLCipherSuite      ECDHE-ECDSA-AES128-GCM-SHA256:
                    ECDHE-RSA-AES128-GCM-SHA256:
                    ECDHE-ECDSA-AES256-GCM-SHA384:
                    ECDHE-RSA-AES256-GCM-SHA384:
                    ECDHE-ECDSA-CHACHA20-POLY1305:
                    ECDHE-RSA-CHACHA20-POLY1305:
                    DHE-RSA-AES128-GCM-SHA256:
                    DHE-RSA-AES256-GCM-SHA384:
                    DHE-RSA-CHACHA20-POLY1305

SSLHonorCipherOrder off
SSLSessionTickets   off
SSLCompression      off

SSLUseStapling      on
SSLStaplingCache    shmcb:/var/run/ocsp(128000)
```

---

## Supported TLS versions

```apache
SSLProtocol -all +TLSv1.2 +TLSv1.3
SSLProxyProtocol -all +TLSv1.2 +TLSv1.3
```

These directives restrict both client connections and upstream proxy connections to **TLS 1.2 and TLS 1.3 only**.

Version       | Status   | Reason
--------------|----------|-----------------------------------------------------------
TLS 1.3       | Enabled  | Latest TLS standard with improved security and performance
TLS 1.2       | Enabled  | Widely supported secure protocol
TLS 1.1 / 1.0 | Disabled | Deprecated and vulnerable to known attacks
SSLv3 / SSLv2 | Disabled | Insecure and obsolete

TLS 1.3 cipher suites are negotiated automatically by OpenSSL and are not controlled by the `SSLCipherSuite` directive.

---

## Elliptic curve selection

```apache
SSLOpenSSLConfCmd Curves X25519:prime256v1:secp384r1
```

Defines the allowed curves for elliptic curve cryptography during TLS key exchange.

Curve                  | Description
-----------------------|------------------------------------------------------------
**X25519**             | Modern high-performance curve preferred by most TLS clients
**prime256v1 (P-256)** | Widely supported NIST curve
**secp384r1 (P-384)**  | Higher strength NIST curve

The server and client negotiate the first mutually supported curve.

---

## Cipher suites (TLS 1.2)

```apache
SSLCipherSuite ECDHE-ECDSA-AES128-GCM-SHA256:...
```

These cipher suites define the allowed cryptographic algorithms for **TLS 1.2 connections**.

All selected suites provide:

* **Forward secrecy**
* **Authenticated encryption (AEAD)**
* **Modern key exchange mechanisms**

### ECDHE cipher suites

Most connections will use **ECDHE (Elliptic Curve Diffie-Hellman Ephemeral)** for key exchange.

Example:

```apache
ECDHE-RSA-AES128-GCM-SHA256
```

Component                  | Meaning
---------------------------|--------------------------------------
ECDHE                      | Ephemeral elliptic curve key exchange
RSA / ECDSA                | Certificate authentication method
AES128 / AES256 / CHACHA20 | Symmetric encryption algorithm
GCM / POLY1305             | Authenticated encryption mode
SHA256 / SHA384            | Handshake hash algorithm

#### AES-GCM suites

* ECDHE-ECDSA-AES128-GCM-SHA256
* ECDHE-RSA-AES128-GCM-SHA256
* ECDHE-ECDSA-AES256-GCM-SHA384
* ECDHE-RSA-AES256-GCM-SHA384

These provide high-performance AES encryption using **Galois/Counter Mode (GCM)**.

#### ChaCha20 suites

* ECDHE-ECDSA-CHACHA20-POLY1305
* ECDHE-RSA-CHACHA20-POLY1305

ChaCha20 performs better than AES on systems without AES hardware acceleration (e.g., many mobile devices).

---

### DHE fallback suites

* DHE-RSA-AES128-GCM-SHA256
* DHE-RSA-AES256-GCM-SHA384
* DHE-RSA-CHACHA20-POLY1305

These use **finite-field Diffie-Hellman** rather than elliptic curves and exist primarily for compatibility with older clients that cannot use ECDHE.

---

## Cipher ordering

```apache
SSLHonorCipherOrder off
```

This allows the **client to choose the preferred cipher suite** from the server’s allowed list.

This behavior is recommended when supporting modern clients because browsers typically select the most optimal cipher for the platform (for example, preferring ChaCha20 on mobile devices).

---

## Session security

### Disable TLS Session Tickets

```apache
SSLSessionTickets off
```

Disabling session tickets prevents reuse of ticket encryption keys across long periods, which can otherwise weaken forward secrecy if ticket keys are compromised.

Session resumption still works using **session IDs**.

---

### Disable TLS compression

```apache
SSLCompression off
```

TLS compression is disabled to prevent attacks such as **CRIME**, which exploit compression side channels.

---

## Security properties of this configuration

This TLS configuration provides the following protections:

Property                      | Description
------------------------------|---------------------------------------
Modern TLS versions           | Only TLS 1.2 and TLS 1.3 permitted
Forward secrecy               | Provided by ECDHE and DHE key exchange
AEAD encryption               | AES-GCM and ChaCha20-Poly1305 only
No legacy algorithms          | CBC, RC4, and 3DES excluded
Secure curves                 | X25519 and modern NIST curves only
Compression attacks prevented | TLS compression disabled
Revocation checking           | OCSP stapling enabled

---

## Summary

This configuration enforces modern TLS best practices:

* Only **TLS 1.2 and TLS 1.3**
* Strong **ECDHE and DHE key exchange**
* **AES-GCM and ChaCha20-Poly1305 authenticated encryption**
* **Secure elliptic curves**
* **OCSP stapling for certificate validation**
* Protection against legacy TLS vulnerabilities

The result is a secure and performant TLS configuration suitable for modern browsers and API clients.
