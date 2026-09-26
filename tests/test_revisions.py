import tempfile, unittest
from pathlib import Path
from src.domain import ConflictError, PermissionDenied, ValidationError
from src.repository import Repository
from src.service import Service
from src.rules import STATES, TRANSITION_ROLES


class RevisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(str(Path(self.tmp.name) / "test.db"))
        self.service = Service(self.repo)
        self.item = self.service.create_item(
            {"title": "revision item", "description": "review revision flow",
             "severity": "exceedance", "quantity": 12, "threshold": 6,
             "external_ref": "REV-1"}, "creator", "operator")

    def tearDown(self):
        self.repo.close(); self.tmp.cleanup()

    def _register(self, **overrides):
        payload = {"field": "quantity", "old_value": 12, "new_value": 3,
                   "material_ref": "MAT-1", "expected_version": self.item["version"]}
        payload.update(overrides)
        return self.service.register_revision(
            self.item["id"], payload, "officer", "compliance_officer")

    def _walk_to_inspection(self):
        current = self.service.get_item(self.item["id"], "viewer")
        for target in STATES[1:-1]:
            current = self.service.transition(
                current["id"], target, current["version"], "reviewer",
                TRANSITION_ROLES[target][0])
        return current

    def test_register_validation(self):
        with self.assertRaises(PermissionDenied):
            self.service.register_revision(
                self.item["id"], {"field": "quantity", "old_value": 12, "new_value": 3,
                                  "material_ref": "MAT-1", "expected_version": 1},
                "attacker", "operator")
        with self.assertRaises(ValidationError):
            self._register(field="status")
        with self.assertRaises(ValidationError):
            self._register(expected_version=0)
        with self.assertRaises(ConflictError):
            self._register(expected_version=99)
        with self.assertRaises(ConflictError):
            self._register(old_value=999)
        with self.assertRaises(ValidationError):
            self._register(new_value=12)
        revision = self._register()
        self.assertEqual(revision["status"], "pending")
        self.assertEqual(revision["batch"], 1)
        self.assertEqual(revision["material_ref"], "MAT-1")
        self.assertEqual(revision["item_version"], self.item["version"])
        self.assertEqual(revision["old_conclusion"]["priority"], self.item["priority"])

    def test_pending_revision_blocks_close(self):
        revision = self._register()
        current = self._walk_to_inspection()
        self.assertFalse(current["can_close"])
        self.assertIn("存在待复核修订，请先处理", current["close_blockers"])
        with self.assertRaises(ConflictError) as ctx:
            self.service.transition(current["id"], STATES[-1], current["version"],
                                    "chief", TRANSITION_ROLES[STATES[-1]][0])
        self.assertIn("待复核修订", str(ctx.exception))
        self.service.confirm_revision(self.item["id"], revision["id"],
                                      "officer2", "compliance_officer")
        current = self.service.get_item(self.item["id"], "viewer")
        self.assertTrue(current["can_close"])
        closed = self.service.transition(current["id"], STATES[-1], current["version"],
                                         "chief", TRANSITION_ROLES[STATES[-1]][0])
        self.assertEqual(closed["status"], STATES[-1])

    def test_confirm_recalculates_and_rejudges(self):
        record = self.service.add_record(
            self.item["id"], {"kind": "remediation", "detail": "unfinished fix",
                              "status": "open", "external_ref": "REV-REC-1"},
            "recorder", "operator")
        self.assertEqual(record["review_batch"], 0)
        old_priority = self.item["priority"]
        old_deadline = self.item["deadline_hours"]
        revision = self._register(new_value=3)
        result = self.service.confirm_revision(
            self.item["id"], revision["id"], "officer2", "compliance_officer")
        applied = result["revision"]
        item = result["item"]
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(applied["applied_by"], "officer2")
        self.assertEqual(item["quantity"], 3.0)
        self.assertLess(item["priority"], old_priority)
        self.assertGreater(item["deadline_hours"], old_deadline)
        self.assertEqual(item["revision_batch"], 1)
        self.assertEqual(applied["old_conclusion"]["priority"], old_priority)
        self.assertEqual(applied["old_conclusion"]["deadline_hours"], old_deadline)
        self.assertEqual(applied["new_conclusion"]["priority"], item["priority"])
        records = self.service.list_records(self.item["id"], "viewer")
        self.assertEqual(records[0]["review_batch"], 1)
        with self.assertRaises(ConflictError):
            self.service.confirm_revision(self.item["id"], revision["id"],
                                          "officer2", "compliance_officer")

    def test_stale_revision_base_rejected(self):
        first = self._register(new_value=3)
        second = self._register(new_value=9)
        self.assertEqual(second["batch"], 2)
        self.service.confirm_revision(self.item["id"], first["id"],
                                      "officer2", "compliance_officer")
        with self.assertRaises(ConflictError):
            self.service.confirm_revision(self.item["id"], second["id"],
                                          "officer2", "compliance_officer")

    def test_list_and_detail_expose_revision_state(self):
        self._register()
        detail = self.service.get_item(self.item["id"], "viewer")
        self.assertEqual(detail["pending_revisions"], 1)
        self.assertEqual(detail["revision_batch"], 0)
        listed = self.service.list_items("viewer")[0]
        self.assertEqual(listed["pending_revisions"], 1)
        self.assertIn("close_blockers", listed)
        revisions = self.service.list_revisions(self.item["id"], "viewer")
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["batch"], 1)
        events = self.service.audit("viewer", self.item["id"])
        self.assertIn("revision_register", [event["action"] for event in events])
        self.assertTrue(self.repo.verify_audit_chain())


if __name__ == "__main__":
    unittest.main()
