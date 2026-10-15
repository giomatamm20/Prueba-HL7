import copy
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app.astm import _checksum, _records, build_astm_result, parse_astm_order, send_astm_tcp
from app.hl7_contract import infer_hl7_contract, validate_hl7_contract
from app.hl7_orders import extract_metadata, hl7_path_value
from app.profile_builder import build_profile_message
from app.protocols import send_mllp
from app.engine import SimulationEngine
from support import EngineCase, OML, ASTM, config, exchange, free_port, outgoing, read_frame, receiver, install_contract, outbound_contract


class ProtocolSocketTests(EngineCase):
    def test_full_cycle_for_documented_device_templates(self):
        # These are configurations of the simulator, not vendor conformance.
        devices = [
            ("CELL_DYN_RUBY", "HL7", "GENERIC"), ("SYSMEX_XN1000", "HL7", "GENERIC"),
            ("ARCHITECT_CI4100", "HL7", "GENERIC"), ("SYSMEX_UC3500", "ASTM", "GENERIC"),
            ("GENERIC_HL7", "HL7", "GENERIC"), ("GENERIC_ASTM", "ASTM", "GENERIC"),
            ("ANAPATH", "HL7", "ANAPATH"),
        ]
        for name, protocol, vendor_profile in devices:
            with self.subTest(device=name, protocol=protocol), receiver(protocol) as destination:
                port = free_port()
                self.manager.configure(config(name, profile=vendor_profile, protocol=protocol, listenPort=port,
                                              resultDestinationPort=destination.server_address[1]))
                if protocol == "HL7":
                    inbound = OML
                    body = {"name": "Inbound-only synthetic contract", "inbound": {
                        "strictValidation": True, "schema": infer_hl7_contract(inbound)}, "outbound": outbound_contract()}
                    self.engine.save_protocol_profile(name, body)
                    self.manager.sync(name)
                    self.assertIn("MSA|AA|MSG-1", exchange(port, inbound, fragments=True))
                else:
                    with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
                        connection.sendall(ASTM.encode())
                        self.assertEqual(connection.recv(1), b"\x06")
                order = next(o for o in self.engine.list_orders() if o["analyzerId"] == name)
                self.assertEqual(order["patientId"], "PAT-SYNTHETIC")
                self.assertEqual(order["barcode"], "SAMPLE-1")
                self.engine.load_sample(order["id"], "SAMPLE-1")
                with patch("app.engine.time.sleep"):
                    completed = self.engine.process_order(order["id"])
                self.assertEqual(completed["status"], "COMPLETED")
                self.assertEqual(completed["lastMessage"]["ackCode"], "AA" if protocol == "HL7" else "ACK")
                self.assertEqual(len(destination.messages), 1)
                raw = destination.messages[0]
                if protocol == "HL7":
                    self.assertEqual(extract_metadata(raw)["messageControlId"], completed["messageControlId"])
                    self.assertIn("SAMPLE-1", raw)
                    self.assertIn("ORU^R01", raw)
                    if vendor_profile == "ANAPATH":
                        self.assertIn("JVBER", raw)
                        self.assertEqual(hl7_path_value(raw, "ORC-2"), "ORDER-1")
                    else:
                        self.assertEqual(hl7_path_value(raw, "OBX-2"), "NM")
                        codes = [line.split("|")[3].split("^")[0] for line in raw.split("\r") if line.startswith("OBX|")]
                        self.assertEqual(codes, [result["code"] for result in completed["results"]])
                else:
                    records = _records(raw)
                    self.assertTrue(any(record.startswith("R|") for record in records))
                    self.assertTrue(records[-1].startswith("L|"))
                self.assertFalse(destination.errors)

    def test_anapath_events_and_report_statuses_preserve_correlation(self):
        with receiver(responses=("AA",)) as destination:
            port = free_port()
            self.manager.configure(config("anapath-events", profile="ANAPATH", listenPort=port,
                resultDestinationPort=destination.server_address[1], behavior={
                    "outboundEvents": ["REPORT", "STATUS_CHANGE", "NEW_SAMPLE", "DELETE_SAMPLE"],
                    "reportStatuses": ["F", "C", "X"]}))
            install_contract(self.engine, "anapath-events")
            active = self.engine.store.get_active_protocol_profile("anapath-events")
            action_schema = copy.deepcopy(outbound_contract()["schema"])
            action_schema["messageType"] = "OML^O21"
            self.engine.update_protocol_profile("anapath-events", active["id"], {**active, "activate": True,
                "outbound": {**outbound_contract(), "eventSchemas": {
                    event: action_schema for event in ("STATUS_CHANGE", "NEW_SAMPLE", "DELETE_SAMPLE")}}})
            self.manager.sync("anapath-events")
            raw = OML.replace("ORC|NW|ORDER-1", "ORC|NW|ORDER-1|FILLER-1|PRESCRIPTION-1")
            self.assertIn("MSA|AA|", exchange(port, raw))
            order = next(o for o in self.engine.list_orders() if o["analyzerId"] == "anapath-events")
            self.engine.load_sample(order["id"], order["barcode"])
            with patch("app.engine.time.sleep"):
                self.engine.process_order(order["id"], False)
            for status in ("F", "C", "X"):
                with self.subTest(reportStatus=status):
                    sent = self.engine.emit_order_event(order["id"], "REPORT", {"reportStatus": status})
                    self.assertEqual(sent["lastMessage"]["ackCode"], "AA")
                    message = destination.messages[-1]
                    self.assertEqual(hl7_path_value(message, "OBX-11"), status)
                    self.assertEqual(hl7_path_value(message, "ORC-2"), "ORDER-1")
                    self.assertEqual(hl7_path_value(message, "ORC-3"), "FILLER-1")
                    self.assertEqual(hl7_path_value(message, "ORC-4"), "PRESCRIPTION-1")
                    self.assertEqual(hl7_path_value(message, "OBR-3"), "SAMPLE-1")
                    self.assertTrue(hl7_path_value(message, "OBX-5").startswith("JVBER") if status != "X" else "CANCELACION" in hl7_path_value(message, "OBX-5"))
            for event, control in (("STATUS_CHANGE", "SC"), ("NEW_SAMPLE", "NW"), ("DELETE_SAMPLE", "CA")):
                with self.subTest(event=event):
                    sent = self.engine.emit_order_event(order["id"], event)
                    self.assertEqual(sent["lastMessage"]["ackCode"], "AA")
                    self.assertEqual(hl7_path_value(destination.messages[-1], "ORC-1"), control)

    def test_pacs_lost_notification_ack_retries_identical_message_after_restart(self):
        with receiver(responses=(None, "AA")) as destination:
            install_contract(self.engine, "pacs-simulator", OML.replace("OML^O21", "ORM^O01"))
            self.engine.update_analyzer("pacs-simulator", {"resultDestinationPort": destination.server_address[1]})
            raw = OML.replace("OML^O21", "ORM^O01")
            self.engine.receive_hl7_order(raw, "pacs-simulator", "127.0.0.1")
            study = self.engine.list_pacs_studies()[0]
            with patch("app.engine.time.sleep"):
                self.engine.process_pacs_study(study["id"])
            self.assertEqual(self.engine.notify_pacs_study(study["id"])["status"], "NOTIFICATION_ERROR")
            # Keep the same receiver through the restart; the destination remains
            # configured in the environment for this PACS-specific override.
            with patch.dict(os.environ, {"PACS_NOTIFICATION_PORT": str(destination.server_address[1])}):
                rebooted = SimulationEngine()
            self.assertEqual(rebooted.notify_pacs_study(study["id"])["status"], "COMPLETED")
            self.assertEqual(destination.messages[0], destination.messages[1])

    def test_outbound_custom_separators_are_validated_and_acknowledged(self):
        self.add()
        self.engine.receive_hl7_order(OML, "test-analyzer", "127.0.0.1")
        order = self.engine.list_orders()[0]
        definition = {"name": "Custom separators", "inbound": {"schema": infer_hl7_contract(OML)}, "outbound": outgoing()}
        self.engine.load_sample(order["id"], order["barcode"])
        with patch("app.engine.time.sleep"):
            self.engine.process_order(order["id"], False)
        definition["outbound"]["events"]["RESULT_FINAL"]["segments"] = definition["outbound"]["events"]["RESULT_FINAL"]["segments"][:3]
        message, _ = build_profile_message(definition, self.engine.analyzers["test-analyzer"], order, [], "RESULT_FINAL", 1)
        custom = message.translate(str.maketrans("|^~\\&", "!$%?@"))
        definition["outbound"]["schema"] = infer_hl7_contract(custom)
        self.engine.save_protocol_profile("test-analyzer", definition)
        with receiver() as destination:
            self.engine.update_analyzer("test-analyzer", {"resultDestinationPort": destination.server_address[1]})
            result = self.engine.send_result(order["id"])
            self.assertEqual(result["status"], "COMPLETED")
            self.assertTrue(destination.messages[0].startswith("MSH!$%?@!"))
            validate_hl7_contract(destination.messages[0], definition["outbound"]["schema"])

    def test_pacs_real_socket_order_image_and_notification(self):
        with receiver() as destination:
            port = free_port()
            install_contract(self.engine, "pacs-simulator", OML.replace("OML^O21", "ORM^O01").replace("CBC^Hemograma", "CT_TEST^Imagen sintetica"))
            self.manager.configure({"listenPort": port, "resultDestinationPort": destination.server_address[1]}, "pacs-simulator")
            raw = OML.replace("OML^O21", "ORM^O01").replace("CBC^Hemograma", "CT_TEST^Imagen sintetica")
            self.assertIn("MSA|AA|", exchange(port, raw))
            study = self.engine.list_pacs_studies()[0]
            self.assertEqual(study["accessionNo"], "SAMPLE-1")
            with patch("app.engine.time.sleep"):
                ready = self.engine.process_pacs_study(study["id"])
            self.assertEqual(ready["status"], "IMAGE_READY")
            completed = self.engine.notify_pacs_study(study["id"])
            self.assertEqual(completed["status"], "COMPLETED")
            self.assertIn("IMAGE_STATUS^ESTADO DE IMAGEN||READY", destination.messages[0])
            self.assertNotIn("DICOM", destination.messages[0])

    def test_strict_outbound_contract_is_checked_before_network_send(self):
        self.add()
        self.engine.receive_hl7_order(OML, "test-analyzer", "127.0.0.1")
        order = self.engine.list_orders()[0]
        self.engine.load_sample(order["id"], "SAMPLE-1")
        with patch("app.engine.time.sleep"):
            self.engine.process_order(order["id"], False)
        definition = {"name": "Full outgoing contract", "inbound": {"schema": infer_hl7_contract(OML)}, "outbound": outgoing()}
        raw, _ = build_profile_message(definition, self.engine.analyzers["test-analyzer"], order,
                                       self.engine.orders[0]["results"], "RESULT_FINAL", 1)
        definition["outbound"]["schema"] = infer_hl7_contract(raw)
        self.engine.save_protocol_profile("test-analyzer", definition)
        with receiver() as destination:
            self.engine.update_analyzer("test-analyzer", {"resultDestinationPort": destination.server_address[1]})
            completed = self.engine.send_result(order["id"])
            self.assertEqual(completed["status"], "COMPLETED")
            validate_hl7_contract(destination.messages[0], definition["outbound"]["schema"])
            broken = copy.deepcopy(definition)
            pid = next(s for s in broken["outbound"]["schema"]["segments"] if s["name"] == "PID")
            pid["fields"]["99"] = {"required": True}
            self.engine.save_protocol_profile("test-analyzer", broken)
            with self.assertRaisesRegex(ValueError, "Contrato HL7"):
                self.engine.send_result(order["id"])
            self.assertEqual(len(destination.messages), 1)

    def test_lost_ack_retry_preserves_bytes_when_contract_has_not_changed(self):
        self.add()
        self.engine.receive_hl7_order(OML, "test-analyzer", "127.0.0.1")
        order = self.engine.list_orders()[0]
        self.engine.load_sample(order["id"], "SAMPLE-1")
        with patch("app.engine.time.sleep"):
            self.engine.process_order(order["id"], False)
        profile = self.engine.save_protocol_profile("test-analyzer", {"name": "Original", "inbound": {"schema": infer_hl7_contract(OML)}, "outbound": outgoing()})
        with receiver(responses=(None, "AA")) as destination:
            self.engine.update_analyzer("test-analyzer", {"resultDestinationPort": destination.server_address[1]})
            first = self.engine.send_result(order["id"])
            self.assertEqual(first["status"], "ERROR")
            restarted = SimulationEngine()
            final = restarted.send_result(order["id"])
            self.assertEqual(final["status"], "COMPLETED")
            self.assertEqual(destination.messages[0], destination.messages[1])
            self.assertEqual(final["lastMessage"]["profileId"], profile["id"])

    def test_wrong_ack_id_negative_ack_unframed_response_and_unavailable_destination(self):
        self.add()
        self.engine.receive_hl7_order(OML, "test-analyzer", "127.0.0.1")
        order = self.engine.list_orders()[0]
        raw, control_id = build_profile_message({"outbound": outgoing()}, self.engine.analyzers["test-analyzer"], order, [], "RESULT_FINAL", 1)
        for options in ({"wrong_id": True}, {"malformed": True}):
            with self.subTest(options=options), receiver(**options) as destination:
                with self.assertRaises((ConnectionError, ValueError)):
                    send_mllp(raw, "127.0.0.1", destination.server_address[1], timeout=0.2)
        with receiver(responses=("AE",)) as destination:
            ack, code = send_mllp(raw, "127.0.0.1", destination.server_address[1], timeout=0.2)
            self.assertEqual(code, "AE")
        self.engine.update_analyzer("test-analyzer", {"resultDestinationPort": free_port()})
        self.engine.load_sample(order["id"], order["barcode"])
        with patch("app.engine.time.sleep"):
            self.engine.process_order(order["id"], False)
        self.assertEqual(self.engine.send_result(order["id"])["status"], "ERROR")

    def test_fragmented_pipelined_messages_and_simultaneous_connections(self):
        port = free_port()
        self.add()
        self.manager.configure({"listenPort": port}, "test-analyzer")
        with ThreadPoolExecutor(max_workers=6) as executor:
            replies = list(executor.map(lambda _: exchange(port, OML, fragments=True), range(12)))
        self.assertTrue(all("MSA|AA|MSG-1" in reply for reply in replies))
        self.assertEqual(len(self.engine.list_orders()), 1)
        with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
            connection.settimeout(2)
            second = OML.replace("MSG-1", "MSG-2").replace("SAMPLE-1", "SAMPLE-2")
            connection.sendall(b"\x0b" + second.encode() + b"\x1c\r\x0b" + second.encode() + b"\x1c\r")
            replies = b""
            while replies.count(b"\x1c\r") < 2:
                replies += connection.recv(8192)
            self.assertEqual(replies.count(b"MSA|AA|MSG-2"), 2)
        self.assertEqual(len(self.engine.list_orders()), 2)

    def test_mllp_invalid_framing_encoding_size_timeout_and_disconnect(self):
        port = free_port()
        with patch.dict(os.environ, {"SIMULATOR_READ_TIMEOUT": "0.1", "SIMULATOR_MAX_MESSAGE_BYTES": "1024"}):
            self.add()
            self.manager.configure({"listenPort": port}, "test-analyzer")
        for payload in (b"unframed", b"\x0b\xff\x1c\r", b"\x0b" + b"X" * 1100, b"\x0bMSH|"):
            with self.subTest(payload=payload[:20]), socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
                connection.settimeout(2)
                connection.sendall(payload)
                self.assertIn(b"MSA|AE|", read_frame(connection))
        with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
            connection.settimeout(2)
            connection.sendall(b"\x0bMSH|")
            connection.shutdown(socket.SHUT_WR)
            self.assertIn(b"MSA|AE|", read_frame(connection))
        self.assertEqual(self.engine.list_orders(), [])
        self.assertTrue(all(m["error"] for m in self.engine.list_messages()))

    def test_utf8_special_characters_and_outbound_delimiter_escaping(self):
        port = free_port()
        self.add()
        self.manager.configure({"listenPort": port}, "test-analyzer")
        raw = OML.replace("PRUEBA^SINTETICA", "MUÑOZ^ÁNGELA\\F\\PRUEBA")
        self.assertIn("MSA|AA|", exchange(port, raw, fragments=True))
        self.assertIn("MUÑOZ", self.engine.list_orders()[0]["patientName"])
        sample = {**self.engine.list_orders()[0], "patientName": "MUÑOZ^ANA|X\rOBX|INJECT"}
        message, _ = build_profile_message({"outbound": outgoing()}, self.engine.analyzers["test-analyzer"], sample, [], "RESULT_FINAL", 1)
        self.assertIn("\\F\\X\\X0D\\OBX\\F\\INJECT", message)
        self.assertEqual(message.count("\rOBX|"), 0)

    def test_astm_framing_checksum_sequence_negative_ack_and_retransmission(self):
        port = free_port()
        self.manager.configure(config(protocol="ASTM", listenPort=port))
        framed = ""
        for n, record in enumerate(ASTM.strip("\r").split("\r"), 1):
            body = str(n % 8) + record + "\r\x03"
            framed += "\x02" + body + _checksum(body) + "\r\n"
        for raw, expected in ((framed, b"\x06"), (framed, b"\x06"),
                              (framed.replace("HIS_TEST", "HIS_FAIL"), b"\x15"), (ASTM.replace("CBC", "BAD"), b"\x15")):
            with self.subTest(expected=expected), socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
                connection.settimeout(2)
                data = raw.encode()
                # Break the first record before its ETX: the listener must wait
                # for the L record and complete checksum bytes.
                for offset in range(0, len(data), 5):
                    connection.sendall(data[offset:offset + 5])
                self.assertEqual(connection.recv(1), expected)
        self.assertEqual(len(self.engine.list_orders()), 1)
        with receiver("ASTM", responses=("AE",)) as destination:
            message, _ = build_astm_result(self.engine.analyzers["test-analyzer"], self.engine.list_orders()[0], [], 1)
            ack, code = send_astm_tcp(message, "127.0.0.1", destination.server_address[1], timeout=0.2)
            self.assertEqual(code, "NAK")
            self.engine.update_analyzer("test-analyzer", {"resultDestinationPort": destination.server_address[1]})
            self.engine.load_sample(self.engine.list_orders()[0]["id"], "SAMPLE-1")
            with patch("app.engine.time.sleep"):
                self.engine.process_order(self.engine.list_orders()[0]["id"], False)
            failed = self.engine.send_result(self.engine.list_orders()[0]["id"])
            self.assertEqual(failed["status"], "ERROR")
