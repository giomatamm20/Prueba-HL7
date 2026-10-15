import os
import socket
from unittest.mock import patch

from starlette.testclient import TestClient

from app import main
from support import EngineCase, OML, config, exchange, free_port
from support import receiver, install_contract, outgoing
from app.hl7_contract import infer_hl7_contract
from app.hl7_orders import hl7_path_value


class ListenerSocketTests(EngineCase):
    def configure(self, payload, identifier=None):
        result = self.manager.configure(payload, identifier)
        if result["protocol"] == "HL7" and not result["contractReady"]:
            install_contract(self.engine, result["id"])
            self.manager.sync(result["id"])
            result = next(a for a in self.engine.list_analyzers() if a["id"] == result["id"])
        return result

    def test_rename_and_destination_edit_keep_both_listeners_and_open_connection(self):
        first, second = free_port(), free_port()
        self.configure(config("a", listenPort=first))
        self.configure(config("b", listenPort=second))
        original_servers = tuple(self.manager.servers)
        with socket.create_connection(("127.0.0.1", second), timeout=2) as connection:
            connection.settimeout(2)
            updated = self.configure({"name": "Edited", "resultDestinationPort": 6000}, "a")
            self.assertTrue(updated["listenerActive"])
            self.assertEqual(tuple(self.manager.servers), original_servers)
            connection.sendall(b"\x0b" + OML.encode() + b"\x1c\r")
            self.assertIn(b"MSA|AA|MSG-1", connection.recv(8192))
        self.assertIn("MSA|AA|", exchange(first, OML))

    def test_port_change_preserves_other_device_and_reports_active_state(self):
        first, second, replacement = free_port(), free_port(), free_port()
        self.configure(config("a", listenPort=first))
        self.configure(config("b", listenPort=second))
        other = self.manager._running["b"][0]
        result = self.configure({"listenPort": replacement}, "a")
        self.assertTrue(result["listenerActive"])
        self.assertIsNone(result["listenerError"])
        self.assertIs(self.manager._running["b"][0], other)
        self.assertIn("MSA|AA|", exchange(second, OML))
        self.assertIn("MSA|AA|", exchange(replacement, OML))
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", first), timeout=0.1)

    def test_occupied_port_is_not_saved_and_old_listener_stays_active(self):
        original = free_port()
        self.configure(config("a", listenPort=original))
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            with self.assertRaises(ValueError):
                self.configure({"listenPort": occupied.getsockname()[1]}, "a")
        self.assertEqual(self.engine.analyzers["a"]["listenPort"], original)
        self.assertEqual(self.engine.store.load_analyzers()["a"]["listenPort"], original)
        self.assertIn("MSA|AA|", exchange(original, OML))

    def test_protocol_change_on_same_port_and_disable_listener(self):
        port = free_port()
        self.configure(config("a", listenPort=port))
        result = self.configure({"protocol": "ASTM", "transport": "TCP_SIMULATED"}, "a")
        self.assertTrue(result["listenerActive"])
        from support import ASTM
        with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
            connection.sendall(ASTM.encode())
            self.assertEqual(connection.recv(1), b"\x06")
        result = self.configure({"listenPort": 0}, "a")
        self.assertFalse(result["listenerActive"])

    def test_reserved_duplicate_out_of_range_and_unpublished_ports(self):
        self.add("a", listenPort=5100)
        for port in (-1, 70000, 8090, 5100):
            with self.subTest(port=port), self.assertRaises(ValueError):
                self.add(f"b-{port}", listenPort=port)
        with patch.dict(os.environ, {"SIMULATOR_ALLOWED_LISTEN_PORTS": "5100-5119"}):
            with self.assertRaises(ValueError):
                self.add("unpublished", listenPort=6200)
            self.add("published", listenPort=5101)


class ApiConfigurationTests(EngineCase):
    def test_device_stays_standby_until_both_contracts_are_complete(self):
        port = free_port()
        with patch.dict(os.environ, {"PACS_ORDER_PORT": "0"}), self.client() as client:
            result = client.post("/api/analyzers", json=config("standby", listenPort=port))
            self.assertEqual(result.status_code, 201, result.text)
            self.assertEqual(result.json()["state"], "STANDBY")
            self.assertFalse(result.json()["listenerActive"])
            self.assertEqual(client.patch("/api/analyzers/standby/state", json={"state": "IDLE"}).status_code, 400)
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", port), timeout=0.1)
            draft = client.post("/api/analyzers/standby/protocol-profiles", json={"name": "Pending",
                "inbound": {"schema": infer_hl7_contract(OML)}, "outbound": {}})
            self.assertEqual(draft.status_code, 201, draft.text)
            self.assertEqual(draft.json()["status"], "DRAFT")
            self.assertEqual(client.post(f"/api/analyzers/standby/protocol-profiles/{draft.json()['id']}/activate").status_code, 400)
            self.assertIn("MSA|AE|", main.engine.receive_hl7_order(OML, "standby", "127.0.0.1"))
            self.assertEqual(client.get("/api/orders").json(), [])
            complete = client.put(f"/api/analyzers/standby/protocol-profiles/{draft.json()['id']}", json={
                "name": "Complete", "inbound": {"schema": infer_hl7_contract(OML)}, "outbound": outgoing()})
            self.assertEqual(complete.status_code, 200, complete.text)
            self.assertEqual(complete.json()["status"], "ACTIVE")
            actual = next(a for a in client.get("/api/analyzers").json() if a["id"] == "standby")
            self.assertTrue(actual["contractReady"])
            self.assertTrue(actual["listenerActive"])
            self.assertIn("MSA|AA|", exchange(port, OML))
            self.assertEqual(client.delete(f"/api/analyzers/standby/protocol-profiles/{complete.json()['id']}").status_code, 204)
            actual = next(a for a in client.get("/api/analyzers").json() if a["id"] == "standby")
            self.assertEqual(actual["state"], "STANDBY")
            self.assertFalse(actual["listenerActive"])

    def test_manual_resend_uses_corrected_contract_without_restarting_listener(self):
        port = free_port()
        with patch.dict(os.environ, {"PACS_ORDER_PORT": "0"}), receiver(responses=("AE", "AA")) as destination:
            with self.client() as client:
                created = client.post("/api/analyzers", json=config("editable", listenPort=port,
                    resultDestinationPort=destination.server_address[1]))
                self.assertEqual(created.status_code, 201, created.text)
                definition = {"name": "Editable", "inbound": {"schema": infer_hl7_contract(OML)}, "outbound": outgoing()}
                initial = client.post("/api/analyzers/editable/protocol-profiles", json=definition).json()
                original_listener = main.listener_manager._running["editable"][0]
                self.assertIn("MSA|AA|", exchange(port, OML))
                self.assertEqual(destination.messages, [])
                order = client.get("/api/orders").json()[0]
                self.assertEqual(client.post(f"/api/orders/{order['id']}/load", json={"barcode": order["barcode"]}).status_code, 200)
                with patch("app.engine.time.sleep"):
                    self.assertEqual(client.post(f"/api/orders/{order['id']}/process", json={"sendResult": False}).status_code, 200)
                first = client.post(f"/api/orders/{order['id']}/send").json()
                self.assertEqual(first["status"], "ERROR")
                definition["outbound"]["events"]["RESULT_FINAL"]["segments"].append({"name": "ZXX", "fields": [
                    {"target": "ZXX-1", "sourceType": "fixed", "value": "CORRECTED_FROM_EDITOR"}]})
                revised = client.put(f"/api/analyzers/editable/protocol-profiles/{initial['id']}", json=definition)
                self.assertEqual(revised.status_code, 200, revised.text)
                self.assertNotEqual(revised.json()["id"], initial["id"])
                self.assertIs(main.listener_manager._running["editable"][0], original_listener)
                second = client.post(f"/api/orders/{order['id']}/send").json()
                self.assertEqual(second["status"], "COMPLETED")
                self.assertEqual(hl7_path_value(destination.messages[1], "ZXX-1"), "CORRECTED_FROM_EDITOR")
                self.assertNotEqual(first["messageControlId"], second["messageControlId"])
                self.assertEqual(second["lastMessage"]["profileId"], revised.json()["id"])
                self.assertEqual(len(client.get("/api/orders").json()), 1)

    def test_new_hl7_device_uses_editor_definition_without_test_catalog_or_vendor_code(self):
        port = free_port()
        inbound = OML.replace("CBC^Hemograma", "NEW_DEVICE_CODE^Configured procedure") + "ZXX|VALUE-FROM-HIS\r"
        outbound = {"events": {"RESULT_FINAL": {"messageType": "ORU^R01", "segments": [
            {"name": "MSH", "fields": [
                {"target": "MSH-9", "sourceType": "fixed", "value": "ORU^R01"},
                {"target": "MSH-10", "source": "system.messageId"},
                {"target": "MSH-12", "sourceType": "fixed", "value": "2.5.1"},
            ]},
            {"name": "PID", "fields": [{"target": "PID-3", "source": "patient.id"}]},
            {"name": "ZXX", "fields": [
                {"target": "ZXX-1", "sourceType": "fixed", "value": "CONFIGURED_RESPONSE"},
                {"target": "ZXX-2", "source": "inbound.ZXX-1", "required": True},
            ]},
        ]}}}
        with patch.dict(os.environ, {"PACS_ORDER_PORT": "0"}), receiver() as destination:
            with self.client() as client:
                created = client.post("/api/analyzers", json={"id": "future-device", "protocol": "HL7", "mode": "BIDIRECTIONAL",
                    "listenPort": port, "resultDestinationHost": "127.0.0.1", "resultDestinationPort": destination.server_address[1]})
                self.assertEqual(created.status_code, 201, created.text)
                self.assertFalse(created.json()["listenerActive"])
                self.assertEqual(created.json()["state"], "STANDBY")
                profile = client.post("/api/analyzers/future-device/protocol-profiles", json={"name": "Configured in editor",
                    "inbound": {"schema": infer_hl7_contract(inbound), "strictValidation": True}, "outbound": outbound})
                self.assertEqual(profile.status_code, 201, profile.text)
                self.assertIn("MSA|AA|", exchange(port, inbound))
                order = next(o for o in client.get("/api/orders").json() if o["analyzerId"] == "future-device")
                self.assertEqual(destination.messages, [])
                self.assertEqual(client.post(f"/api/orders/{order['id']}/send").status_code, 400)
                self.assertEqual(client.post(f"/api/orders/{order['id']}/load", json={"barcode": order["barcode"]}).status_code, 200)
                with patch("app.engine.time.sleep"):
                    self.assertEqual(client.post(f"/api/orders/{order['id']}/process", json={"sendResult": False}).status_code, 200)
                response = client.post(f"/api/orders/{order['id']}/send")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "COMPLETED")
                self.assertEqual(hl7_path_value(destination.messages[0], "ZXX-1"), "CONFIGURED_RESPONSE")
                self.assertEqual(hl7_path_value(destination.messages[0], "ZXX-2"), "VALUE-FROM-HIS")
                self.assertEqual(hl7_path_value(destination.messages[0], "PID-3"), "PAT-SYNTHETIC")

    def client(self, address="127.0.0.1"):
        # TestClient's default host is 'testclient', not a routable IP. Supply
        # the peer address in ASGI scope rather than trusting forwarded headers.
        async def app(scope, receive, send):
            if scope["type"] == "http":
                scope = {**scope, "client": (address, 12345)}
            await main.app(scope, receive, send)
        return TestClient(app)

    def test_sparse_put_preserves_configuration(self):
        with patch.dict(os.environ, {"ORDER_LISTENERS_ENABLED": "false"}):
            with self.client() as client:
                response = client.post("/api/analyzers", json=config(listenPort=5100, resultDestinationPort=6200))
                self.assertEqual(response.status_code, 201, response.text)
                response = client.put("/api/analyzers/test-analyzer", json={"name": "Edited"})
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                self.assertEqual((data["listenPort"], data["resultDestinationPort"], data["mode"]), (5100, 6200, "BIDIRECTIONAL"))
                self.assertEqual(client.post("/api/analyzers", json=config("invalid", listenPort=-1)).status_code, 422)

    def test_pool_listing_and_health_status(self):
        with patch.dict(os.environ, {"ORDER_LISTENERS_ENABLED": "false"}):
            with self.client() as client:
                with patch.dict(os.environ, {"SIMULATOR_ALLOWED_LISTEN_PORTS": "5100-5119"}):
                    self.assertEqual(client.post("/api/analyzers", json=config(listenPort=5100)).status_code, 201)
                    pool = client.get("/api/ports").json()
                    self.assertTrue(pool["restricted"])
                    self.assertEqual(len(pool["ports"]), 20)
                    self.assertEqual(pool["ports"][0]["analyzerId"], "test-analyzer")
                self.assertEqual(client.get("/api/health").json()["status"], "ok")
