import contextlib
import http.client
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

from agent_team.__main__ import _set_password
from agent_team.auth import OwnerAuth, hash_password, public_settings, verify_password
from agent_team.keys import KeyVault
from agent_team.server import App, Server
from agent_team.store import Store

HOST = "agent.example.run.app"
PASSWORD = "correct horse battery staple"
SECRET = "s" * 40


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class PasswordTests(unittest.TestCase):
    def test_a_password_checks_against_its_hash_and_nothing_else(self):
        stored = hash_password(PASSWORD)

        self.assertTrue(verify_password(PASSWORD, stored))
        self.assertFalse(verify_password("wrong", stored))
        self.assertNotIn(PASSWORD, stored)

    def test_the_same_password_gets_a_different_salt_each_time(self):
        self.assertNotEqual(hash_password(PASSWORD), hash_password(PASSWORD))

    def test_a_malformed_stored_hash_refuses_rather_than_crashing(self):
        self.assertFalse(verify_password(PASSWORD, "not-a-hash"))
        self.assertFalse(verify_password(PASSWORD, "scrypt$oops"))


class SessionTests(unittest.TestCase):
    def test_a_session_is_valid_until_it_expires(self):
        clock = Clock()
        auth = OwnerAuth(hash_password(PASSWORD), SECRET, HOST, clock=clock)
        cookie = auth.new_session()

        self.assertTrue(auth.valid_session(cookie))
        clock.now += 12 * 3600 + 1
        self.assertFalse(auth.valid_session(cookie))

    def test_a_changed_session_is_refused(self):
        auth = OwnerAuth(hash_password(PASSWORD), SECRET, HOST, clock=Clock())
        cookie = auth.new_session()
        expires, signature = cookie.split(".")
        forged = f"{int(expires) + 999999}.{signature}"

        self.assertFalse(auth.valid_session(forged))
        self.assertFalse(auth.valid_session(""))
        self.assertFalse(auth.valid_session("garbage"))

    def test_a_session_from_another_secret_is_refused(self):
        clock = Clock()
        cookie = OwnerAuth(hash_password(PASSWORD), SECRET, HOST, clock=clock).new_session()
        other = OwnerAuth(hash_password(PASSWORD), "t" * 40, HOST, clock=clock)

        self.assertFalse(other.valid_session(cookie))


class LimiterTests(unittest.TestCase):
    def test_too_many_wrong_passwords_block_every_login_for_a_while(self):
        clock = Clock()
        auth = OwnerAuth(hash_password(PASSWORD), SECRET, HOST, clock=clock)

        for _ in range(10):
            self.assertFalse(auth.blocked())
            auth.record_failure()
        self.assertTrue(auth.blocked())

        clock.now += 15 * 60 + 1
        self.assertFalse(auth.blocked())


class PublicServerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.clock = Clock()
        self.store = Store(root / "data" / "agent_team.db")
        self.addCleanup(self.store.db.close)
        auth = OwnerAuth(hash_password(PASSWORD), SECRET, HOST, clock=self.clock)
        app = App(self.store, KeyVault(root / "data", master_key_path=root / "master.key"), "", auth=auth)
        self.server = Server(0, app)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port

    def request(self, method, path, body=None, headers=None, host=HOST):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(connection.close)
        sent = {"Host": host, **(headers or {})}
        connection.request(method, path, body=body, headers=sent)
        response = connection.getresponse()
        return response.status, response, response.read()

    def login(self, password=PASSWORD, origin=f"https://{HOST}"):
        form = f"password={password}".encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if origin is not None:
            headers["Origin"] = origin
        return self.request("POST", "/login", form, headers)

    def test_the_page_asks_for_the_password_before_anything_else(self):
        status, _, body = self.request("GET", "/")

        self.assertEqual(status, 200)
        self.assertIn('action="/login"', body.decode())
        self.assertNotIn("app.js", body.decode())

    def test_the_api_refuses_a_stranger(self):
        self.assertEqual(self.request("GET", "/api/health")[0], 401)

    def test_a_wrong_password_is_refused_and_sets_no_cookie(self):
        status, response, _ = self.login(password="wrong")

        self.assertEqual(status, 401)
        self.assertIsNone(response.getheader("Set-Cookie"))

    def test_the_right_password_sets_a_locked_down_cookie_and_opens_the_api(self):
        status, response, _ = self.login()
        cookie = response.getheader("Set-Cookie")

        self.assertEqual(status, 303)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Strict", cookie)
        session = cookie.split(";")[0]
        status, _, body = self.request("GET", "/api/health", headers={"Cookie": session})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["database"], "ok")

    def test_a_request_for_another_host_is_refused(self):
        self.assertEqual(self.request("GET", "/", host="evil.example")[0], 403)

    def test_a_login_from_another_origin_is_refused(self):
        self.assertEqual(self.login(origin="https://evil.example")[0], 403)
        self.assertEqual(self.login(origin=None)[0], 403)

    def test_after_too_many_wrong_passwords_even_the_right_one_is_refused(self):
        for _ in range(10):
            self.login(password="wrong")

        self.assertEqual(self.login()[0], 429)

    def test_logout_clears_the_cookie(self):
        _, response, _ = self.request("POST", "/logout", b"", {"Origin": f"https://{HOST}"})

        self.assertIn("Max-Age=0", response.getheader("Set-Cookie"))


if __name__ == "__main__":
    unittest.main()


class PublicSettingsTests(unittest.TestCase):
    def test_without_a_public_host_the_server_stays_private(self):
        self.assertIsNone(public_settings({}))

    def test_public_mode_needs_the_password_hash_and_the_secret(self):
        with self.assertRaisesRegex(ValueError, "AGENT_TEAM_OWNER_PASSWORD_HASH, AGENT_TEAM_SESSION_SECRET"):
            public_settings({"AGENT_TEAM_PUBLIC_HOST": HOST})

    def test_a_short_session_secret_is_refused(self):
        environ = {"AGENT_TEAM_PUBLIC_HOST": HOST, "AGENT_TEAM_OWNER_PASSWORD_HASH": "x", "AGENT_TEAM_SESSION_SECRET": "short"}
        with self.assertRaisesRegex(ValueError, "at least 32 characters"):
            public_settings(environ)

    def test_complete_settings_are_read(self):
        environ = {
            "AGENT_TEAM_PUBLIC_HOST": HOST,
            "AGENT_TEAM_OWNER_PASSWORD_HASH": "scrypt$x",
            "AGENT_TEAM_SESSION_SECRET": SECRET,
        }

        self.assertEqual(public_settings(environ), {"host": HOST, "password_hash": "scrypt$x", "secret": SECRET})


class SetPasswordTests(unittest.TestCase):
    def test_the_printed_hash_checks_the_password_it_was_made_from(self):
        output = io.StringIO()
        answers = iter([PASSWORD, PASSWORD])
        with contextlib.redirect_stdout(output):
            self.assertEqual(_set_password(read=lambda prompt: next(answers)), 0)

        self.assertTrue(verify_password(PASSWORD, output.getvalue().strip()))

    def test_mismatched_passwords_are_refused(self):
        answers = iter([PASSWORD, PASSWORD + "!"])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(_set_password(read=lambda prompt: next(answers)), 2)

    def test_a_short_password_is_refused(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(_set_password(read=lambda prompt: "short"), 2)
