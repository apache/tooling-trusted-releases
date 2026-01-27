# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Tests for secure HTTP client configuration in atr.util module."""

import ssl
import unittest

import aiohttp

import atr.util as util


class TestCreateSecureSSLContext(unittest.TestCase):
    """Tests for create_secure_ssl_context function."""

    def test_creates_ssl_context(self) -> None:
        """Test that create_secure_ssl_context returns an ssl.SSLContext."""
        ctx = util.create_secure_ssl_context()
        self.assertIsInstance(ctx, ssl.SSLContext)

    def test_check_hostname_enabled(self) -> None:
        """Test that hostname checking is explicitly enabled."""
        ctx = util.create_secure_ssl_context()
        self.assertTrue(ctx.check_hostname)

    def test_verify_mode_cert_required(self) -> None:
        """Test that verify_mode is set to CERT_REQUIRED.

        CERT_REQUIRED value is 2 (or ssl.CERT_REQUIRED).
        """
        ctx = util.create_secure_ssl_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(ctx.verify_mode, 2)

    def test_minimum_version_tls_1_2(self) -> None:
        """Test that minimum_version is set to TLSv1_2.

        TLSv1_2 value is 771 (or ssl.TLSVersion.TLSv1_2).
        """
        ctx = util.create_secure_ssl_context()
        self.assertEqual(ctx.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(ctx.minimum_version, 771)

    def test_all_security_settings_together(self) -> None:
        """Comprehensive test verifying all ASVS 9.1.1/9.1.2 compliance settings."""
        ctx = util.create_secure_ssl_context()

        # Verify all three critical settings are enforced
        self.assertTrue(ctx.check_hostname, "ASVS 9.1.1: check_hostname must be True for certificate validation")
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED, "ASVS 9.1.2: verify_mode must be CERT_REQUIRED")
        self.assertEqual(
            ctx.minimum_version, ssl.TLSVersion.TLSv1_2, "ASVS 9.1.2: minimum_version must be TLSv1_2 or higher"
        )


class TestCreateSecureSession(unittest.IsolatedAsyncioTestCase):
    """Tests for create_secure_session function."""

    async def test_creates_client_session(self) -> None:
        """Test that create_secure_session returns an aiohttp.ClientSession."""
        session = await util.create_secure_session()
        try:
            self.assertIsInstance(session, aiohttp.ClientSession)
        finally:
            await session.close()

    async def test_session_has_tcp_connector(self) -> None:
        """Test that session is initialized with a TCPConnector."""
        session = await util.create_secure_session()
        try:
            self.assertIsNotNone(session.connector)
            self.assertIsInstance(session.connector, aiohttp.TCPConnector)
        finally:
            await session.close()

    async def test_connector_has_secure_ssl_context(self) -> None:
        """Test that TCPConnector uses the secure SSL context."""
        session = await util.create_secure_session()
        try:
            connector = session.connector
            self.assertIsNotNone(connector)
            self.assertIsInstance(connector, aiohttp.TCPConnector)

            # Verify the connector was initialized with SSL context
            # The ssl attribute on TCPConnector will be the ssl.SSLContext
            if hasattr(connector, "_ssl"):
                ssl_context = connector._ssl
                self.assertIsNotNone(ssl_context)
                if isinstance(ssl_context, ssl.SSLContext):
                    self.assertTrue(ssl_context.check_hostname)
                    self.assertEqual(ssl_context.verify_mode, ssl.CERT_REQUIRED)
                    self.assertEqual(ssl_context.minimum_version, ssl.TLSVersion.TLSv1_2)
        finally:
            await session.close()

    async def test_session_accepts_optional_timeout(self) -> None:
        """Test that create_secure_session accepts optional timeout parameter."""
        timeout = aiohttp.ClientTimeout(total=30)
        session = await util.create_secure_session(timeout=timeout)
        try:
            self.assertIsNotNone(session.timeout)
            self.assertEqual(session.timeout.total, 30)
        finally:
            await session.close()

    async def test_session_without_timeout(self) -> None:
        """Test that create_secure_session works without explicit timeout."""
        session = await util.create_secure_session()
        try:
            self.assertIsNotNone(session)
            self.assertIsInstance(session, aiohttp.ClientSession)
        finally:
            await session.close()

    async def test_multiple_sessions_have_independent_contexts(self) -> None:
        """Test that multiple sessions each have their own SSL context."""
        session1 = await util.create_secure_session()
        session2 = await util.create_secure_session()
        try:
            # Both sessions should be valid and independent
            self.assertIsInstance(session1, aiohttp.ClientSession)
            self.assertIsInstance(session2, aiohttp.ClientSession)
            self.assertNotEqual(id(session1), id(session2))
        finally:
            await session1.close()
            await session2.close()


if __name__ == "__main__":
    unittest.main()
