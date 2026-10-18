import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from agent_team.keys import KeyVault
from agent_team.network import lan_address
from agent_team.server import App, Server, startup_line
from agent_team.store import Store

TOKEN = "lan-token-0123456789"
LAN_IP = "192.168.1.20"


class LanTestCase(unittest.TestCase):
    def start(self, lan=False):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        store = Store(root / "data" / "agent_team.db")
        self.addCleanup(store.db.close)
        app = App(store, KeyVault(root / "data", master_key_path=root / "master.key"), TOKEN)
        self.server = Server(0, app)
        if lan:
            self.server.allowed_hosts |= {f"{LAN_IP}:{self.server.server_port}"}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port

    def request(self, method, path, host, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(connection.close)
        connection.request(method, path, body=body, headers={"Host": host, "X-Session-Token": TOKEN, **(headers or {})})
        response = connection.getresponse()
        return response.status, response.read()


class LanAccessTests(LanTestCase):
    def test_a_phone_on_the_same_network_can_use_the_site_with_the_token(self):
        self.start(lan=True)
        status, body = self.request("GET", "/api/health", f"{LAN_IP}:{self.port}")

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["database"], "ok")

    def test_a_phone_on_the_network_can_change_things_from_the_site_itself(self):
        self.start(lan=True)
        origin = f"http://{LAN_IP}:{self.port}"
        status, _ = self.request(
            "POST", "/api/keys", f"{LAN_IP}:{self.port}", json.dumps({"provider": "stub", "key": "sk-lan-1"}), {"Origin": origin}
        )

        self.assertEqual(status, 200)

    def test_another_address_on_the_network_is_refused(self):
        self.start(lan=True)

        self.assertEqual(self.request("GET", "/api/health", f"10.9.9.9:{self.port}")[0], 403)

    def test_the_private_server_still_refuses_the_network_address(self):
        self.start(lan=False)

        self.assertEqual(self.request("GET", "/api/health", f"{LAN_IP}:{self.port}")[0], 403)


class LanAddressTests(unittest.TestCase):
    def test_the_address_is_the_one_the_machine_uses_on_the_network(self):
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        fake.getsockname.return_value = (LAN_IP, 0)
        with mock.patch("socket.socket", return_value=fake):
            self.assertEqual(lan_address(), LAN_IP)

    def test_no_network_gives_no_address(self):
        with mock.patch("socket.socket", side_effect=OSError("no network")):
            self.assertIsNone(lan_address())


class NameTests(LanTestCase):
    def test_the_page_is_titled_detto(self):
        self.start()
        status, body = self.request("GET", "/", f"127.0.0.1:{self.port}")

        self.assertEqual(status, 200)
        self.assertIn("<title>Detto</title>", body.decode())

    def test_the_startup_line_says_detto(self):
        self.assertIn("Detto is running", startup_line(None, 8765, TOKEN, None))
        self.assertIn(f"http://{LAN_IP}:8765/?token={TOKEN}", startup_line(None, 8765, TOKEN, LAN_IP))


if __name__ == "__main__":
    unittest.main()
