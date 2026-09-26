import tempfile, unittest
from pathlib import Path
from src.domain import ConflictError, PermissionDenied
from src.repository import Repository
from src.service import Service
from src.rules import STATES, TRANSITION_ROLES


class RevisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(str(Path(self.tmp.name) / "test.db"))
        self.service = Service(self.repo)
        self.item = self.service.create_item(
            {"title": "revision item", "description": "monitoring correction",
             "severity": "exceedance", "quantity": 12, "threshold": 6,
             "external_ref": "REV-1"}, "creator", "operator")

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def _advance_to_inspection(self, item):
        current = item
        for target in STATES[1:-1]:
            current = self.service.transition(
                current["id"], target, current["version"], "reviewer",
                TRANSITION_ROLES[target][0])
        return current

    def test_revision_recalculates_rejudges_and_unblocks_close(self):
        self.service.add_record(
            self.item["id"], {"kind": "fix", "detail": "rectification task",
                              "status": "open", "external_ref": "REV-REC-1"},
            "recorder", "operator")
        current = self._advance_to_inspection(self.item)
        revision = self.service.register_revision(
            current["id"], {"original_value": 12, "new_value": 3,
                            "material_ref": "MAT-9",
                            "expected_version": current["version"]},
            "officer", "compliance_officer")
        self.assertEqual(revision["status"], "pending")
        self.assertEqual(revision["batch"], 1)
        view = self.service.get_item(current["id"], "viewer")
        self.assertEqual(view["pending_revisions"], 1)
        self.assertFalse(view["can_close"])
        self.assertIn("存在待复核修订，请先处理", view["close_blockers"])
        with self.assertRaises(ConflictError):
            self.service.transition(current["id"], STATES[-1], current["version"],
                                    "chief", "director")
        result = self.service.apply_revision(current["id"], revision["id"],
                                             "officer2", "compliance_officer")
        item = result["item"]
        self.assertEqual(item["quantity"], 3)
        self.assertFalse(item["escalation_required"])
        self.assertLess(item["priority"], view["priority"])
        self.assertGreater(item["deadline_hours"], view["deadline_hours"])
        self.assertEqual(item["revision_batch"], 1)
        self.assertEqual(item["pending_revisions"], 0)
        self.assertEqual(result["closed_record_ids"], [1])
        records = self.service.list_records(current["id"], "viewer")
        self.assertEqual(records[0]["status"], "closed")
        applied = result["revision"]
        self.assertEqual(applied["status"], "applied")
        self.assertTrue(applied["old_conclusion"]["superseded"])
        self.assertEqual(applied["old_conclusion"]["quantity"], 12)
        self.assertEqual(applied["new_conclusion"]["quantity"], 3)
        self.assertTrue(item["can_close"])
        closed = self.service.transition(current["id"], STATES[-1], item["version"],
                                         "chief", "director")
        self.assertEqual(closed["status"], STATES[-1])
        revisions = self.service.list_revisions(current["id"], "viewer")
        self.assertEqual([r["batch"] for r in revisions], [1])
        self.assertTrue(self.repo.verify_audit_chain())

    def test_registration_guards(self):
        with self.assertRaises(PermissionDenied):
            self.service.register_revision(
                self.item["id"], {"original_value": 12, "new_value": 3,
                                  "material_ref": "M", "expected_version": 1},
                "op", "operator")
        with self.assertRaises(ConflictError):
            self.service.register_revision(
                self.item["id"], {"original_value": 12, "new_value": 3,
                                  "material_ref": "M", "expected_version": 99},
                "officer", "compliance_officer")
        with self.assertRaises(ConflictError):
            self.service.register_revision(
                self.item["id"], {"original_value": 999, "new_value": 3,
                                  "material_ref": "M", "expected_version": 1},
                "officer", "compliance_officer")
        self.service.register_revision(
            self.item["id"], {"original_value": 12, "new_value": 20,
                              "material_ref": "MAT-1", "expected_version": 1},
            "officer", "compliance_officer")
        with self.assertRaises(ConflictError):
            self.service.register_revision(
                self.item["id"], {"original_value": 12, "new_value": 5,
                                  "material_ref": "MAT-2", "expected_version": 1},
                "officer", "compliance_officer")

    def test_reject_keeps_old_conclusion_and_clears_pending(self):
        revision = self.service.register_revision(
            self.item["id"], {"original_value": 12, "new_value": 3,
                              "material_ref": "MAT-1", "expected_version": 1},
            "officer", "compliance_officer")
        rejected = self.service.reject_revision(
            self.item["id"], revision["id"], {"reason": "材料不全"},
            "director", "director")
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["review_note"], "材料不全")
        item = self.service.get_item(self.item["id"], "viewer")
        self.assertEqual(item["quantity"], 12)
        self.assertEqual(item["pending_revisions"], 0)
        self.assertEqual(item["revision_batch"], 0)
        with self.assertRaises(ConflictError):
            self.service.apply_revision(self.item["id"], revision["id"],
                                        "officer", "compliance_officer")

    def test_apply_without_rejudge_keeps_open_records_blocking(self):
        self.service.add_record(
            self.item["id"], {"kind": "fix", "detail": "rectify",
                              "status": "open", "external_ref": "REV-REC-2"},
            "recorder", "operator")
        current = self._advance_to_inspection(self.item)
        revision = self.service.register_revision(
            current["id"], {"original_value": 12, "new_value": 18,
                            "material_ref": "MAT-2",
                            "expected_version": current["version"]},
            "officer", "compliance_officer")
        result = self.service.apply_revision(current["id"], revision["id"],
                                             "officer", "compliance_officer")
        self.assertEqual(result["closed_record_ids"], [])
        item = result["item"]
        self.assertTrue(item["escalation_required"])
        self.assertFalse(item["can_close"])
        self.assertIn("仍有未关闭事项", item["close_blockers"])
        with self.assertRaises(ConflictError):
            self.service.transition(current["id"], STATES[-1], item["version"],
                                    "chief", "director")

    def test_closed_item_rejects_revision_and_list_shows_revision_fields(self):
        current = self.item
        for target in STATES[1:]:
            current = self.service.transition(
                current["id"], target, current["version"], "reviewer",
                TRANSITION_ROLES[target][0])
        with self.assertRaises(ConflictError):
            self.service.register_revision(
                current["id"], {"original_value": 12, "new_value": 3,
                                "material_ref": "M",
                                "expected_version": current["version"]},
                "officer", "compliance_officer")
        listed = self.service.list_items("viewer")
        self.assertIn("revision_batch", listed[0])
        self.assertIn("pending_revisions", listed[0])
        self.assertIn("can_close", listed[0])
        self.assertIn("close_blockers", listed[0])
        self.assertFalse(listed[0]["can_close"])
        self.assertIn("事件已关闭", listed[0]["close_blockers"])


if __name__ == "__main__":
    unittest.main()
