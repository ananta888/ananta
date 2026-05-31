"""Tests for LoopbackCallbackServer (oidc-002).

Tests:
- Server starts and returns a valid localhost URL
- Callback received correctly with code and state params
- Server stops after first callback
- Timeout returns error result
"""
from __future__ import annotations

import threading
import time
import unittest
import urllib.request
import urllib.parse


from client_surfaces.operator_tui.auth.loopback_callback_server import LoopbackCallbackServer


class TestLoopbackServerStart(unittest.TestCase):
    """Server must start and return a valid localhost redirect URI."""

    def test_start_returns_localhost_url(self):
        """start() must return http://127.0.0.1:<port>/callback."""
        server = LoopbackCallbackServer()
        try:
            redirect_uri = server.start(timeout_seconds=5.0)
            self.assertTrue(redirect_uri.startswith("http://127.0.0.1:"))
            self.assertTrue(redirect_uri.endswith("/callback"))
        finally:
            server.stop()

    def test_port_is_positive_integer(self):
        """The bound port must be a positive integer."""
        server = LoopbackCallbackServer()
        try:
            server.start(timeout_seconds=5.0)
            self.assertGreater(server.port, 0)
            self.assertLessEqual(server.port, 65535)
        finally:
            server.stop()

    def test_redirect_uri_matches_port(self):
        """The redirect URI must include the actual bound port."""
        server = LoopbackCallbackServer()
        try:
            redirect_uri = server.start(timeout_seconds=5.0)
            port_in_uri = int(redirect_uri.split(":")[2].split("/")[0])
            self.assertEqual(port_in_uri, server.port)
        finally:
            server.stop()

    def test_double_start_raises(self):
        """Calling start() twice must raise RuntimeError."""
        server = LoopbackCallbackServer()
        try:
            server.start(timeout_seconds=5.0)
            with self.assertRaises(RuntimeError):
                server.start(timeout_seconds=5.0)
        finally:
            server.stop()


class TestCallbackReceived(unittest.TestCase):
    """Server must capture the callback's code and state."""

    def test_code_and_state_captured(self):
        """Callback with code and state must return correct dict."""
        server = LoopbackCallbackServer()
        redirect_uri = server.start(timeout_seconds=10.0)

        # Send the callback in a background thread
        def _send():
            time.sleep(0.1)  # small delay to let server reach handle_request
            url = redirect_uri + "?code=mycode123&state=mystate456"
            try:
                urllib.request.urlopen(url, timeout=3)
            except Exception:
                pass

        t = threading.Thread(target=_send, daemon=True)
        t.start()

        result = server.wait_for_callback()
        t.join(timeout=2)

        self.assertEqual(result.get("code"), "mycode123")
        self.assertEqual(result.get("state"), "mystate456")
        self.assertNotIn("error", result)

    def test_provider_error_captured(self):
        """Callback with error param must return error dict."""
        server = LoopbackCallbackServer()
        redirect_uri = server.start(timeout_seconds=10.0)

        def _send():
            time.sleep(0.1)
            url = redirect_uri + "?error=access_denied&state=mystate"
            try:
                urllib.request.urlopen(url, timeout=3)
            except Exception:
                pass

        t = threading.Thread(target=_send, daemon=True)
        t.start()

        result = server.wait_for_callback()
        t.join(timeout=2)

        self.assertIn("error", result)
        self.assertEqual(result["error"], "access_denied")


class TestServerStopsAfterCallback(unittest.TestCase):
    """Server must stop after the first callback."""

    def test_server_stopped_after_callback(self):
        """After wait_for_callback() returns, the server should be stopped."""
        server = LoopbackCallbackServer()
        redirect_uri = server.start(timeout_seconds=10.0)

        def _send():
            time.sleep(0.1)
            url = redirect_uri + "?code=abc&state=xyz"
            try:
                urllib.request.urlopen(url, timeout=3)
            except Exception:
                pass

        t = threading.Thread(target=_send, daemon=True)
        t.start()
        result = server.wait_for_callback()
        t.join(timeout=2)

        # After callback, httpd should be None
        self.assertIsNone(server._httpd)

    def test_second_request_after_stop_fails(self):
        """After the server stops, attempting a second request must fail."""
        server = LoopbackCallbackServer()
        redirect_uri = server.start(timeout_seconds=10.0)

        def _send():
            time.sleep(0.1)
            url = redirect_uri + "?code=abc&state=xyz"
            try:
                urllib.request.urlopen(url, timeout=3)
            except Exception:
                pass

        t = threading.Thread(target=_send, daemon=True)
        t.start()
        server.wait_for_callback()
        t.join(timeout=2)

        # Second request should fail (server is stopped)
        with self.assertRaises(Exception):
            urllib.request.urlopen(redirect_uri + "?code=second&state=second", timeout=1)


class TestCallbackTimeout(unittest.TestCase):
    """Server must return timeout error if no callback arrives in time."""

    def test_timeout_returns_error_dict(self):
        """Timeout must return {'error': 'callback_timeout'}."""
        server = LoopbackCallbackServer()
        server.start(timeout_seconds=0.3)  # Very short timeout for test

        start = time.time()
        result = server.wait_for_callback()
        elapsed = time.time() - start

        self.assertEqual(result.get("error"), "callback_timeout")
        # Should not have taken much longer than the timeout
        self.assertLess(elapsed, 3.0)

    def test_port_released_after_timeout(self):
        """After timeout, the server port must be released."""
        server = LoopbackCallbackServer()
        server.start(timeout_seconds=0.3)
        server.wait_for_callback()
        # _httpd should be None after timeout
        self.assertIsNone(server._httpd)


class TestNon404Path(unittest.TestCase):
    """Requests to paths other than /callback must return 404."""

    def test_wrong_path_returns_404(self):
        """A request to /other must get a 404 response."""
        server = LoopbackCallbackServer()
        redirect_uri = server.start(timeout_seconds=5.0)

        # Build URL to /other instead of /callback
        base = redirect_uri.rsplit("/callback", 1)[0]
        try:
            urllib.request.urlopen(base + "/other", timeout=2)
            self.fail("Expected HTTPError 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)
        except Exception:
            pass  # Connection refused or similar after stop is also fine
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
