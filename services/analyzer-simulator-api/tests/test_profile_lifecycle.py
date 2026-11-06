"""Named revisions and logical deletion preserve existing order references."""
from app.engine import SimulationEngine
from app.hl7_contract import infer_hl7_contract
from support import EngineCase, OML, outgoing


class ProfileLifecycleTests(EngineCase):
    def definition(self, name):
        return {"name": name, "inbound": {"schema": infer_hl7_contract(OML)}, "outbound": outgoing()}

    def test_named_revisions_and_deletion_survive_restart(self):
        self.add(contract=False)
        first = self.engine.save_protocol_profile("test-analyzer", self.definition("  Equipo de orina  "))
        second = self.engine.update_protocol_profile("test-analyzer", first["id"], self.definition("Equipo de orina ajustado"))
        self.assertEqual(first["name"], "Equipo de orina")
        self.assertEqual(second["name"], "Equipo de orina ajustado")
        self.engine.delete_protocol_profile("test-analyzer", first["id"])
        restarted = SimulationEngine()
        visible = restarted.list_protocol_profiles("test-analyzer")
        self.assertEqual([(p["id"], p["name"]) for p in visible], [(second["id"], "Equipo de orina ajustado")])
        retained = next(p for p in restarted.store.list_protocol_profiles("test-analyzer") if p["id"] == first["id"])
        self.assertEqual(retained["status"], "RETIRED")
        for action in (lambda: restarted.activate_protocol_profile("test-analyzer", first["id"]),
                       lambda: restarted.update_protocol_profile("test-analyzer", first["id"], self.definition("No resucitar"))):
            with self.assertRaisesRegex(ValueError, "not found"):
                action()
        restarted.delete_protocol_profile("test-analyzer", second["id"])
        self.assertEqual(restarted.list_protocol_profiles("test-analyzer"), [])
        self.assertFalse(restarted.communication_ready("test-analyzer"))
        self.assertEqual(restarted.analyzers["test-analyzer"]["state"], "STANDBY")
        third = restarted.save_protocol_profile("test-analyzer", self.definition("Contrato nuevo"))
        self.assertGreater(third["version"], second["version"])

    def test_blank_name_cannot_replace_active_revision(self):
        self.add()
        active = self.engine.store.get_active_protocol_profile("test-analyzer")
        with self.assertRaisesRegex(ValueError, "nombre"):
            self.engine.update_protocol_profile("test-analyzer", active["id"], self.definition("  "))
        self.assertEqual(self.engine.store.get_active_protocol_profile("test-analyzer")["id"], active["id"])
