import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RULES = (ROOT / "firestore.rules").read_text(encoding="utf-8")
TICKETS_JS = (ROOT / "tickets.js").read_text(encoding="utf-8")


class FirestoreRulesTest(unittest.TestCase):
    def test_other_is_allowed_only_with_its_stable_key(self) -> None:
        self.assertIn('request.resource.data.lab == "Other"', RULES)
        self.assertIn('request.resource.data.labKey == "other"', RULES)

    def test_other_ticket_remains_visible_to_its_creator(self) -> None:
        self.assertIn("resource.data.createdByUid == request.auth.uid", RULES)
        self.assertIn('addScope("createdByUid", "==", currentUser?.uid)', TICKETS_JS)

    def test_technician_role_and_alias_are_supported(self) -> None:
        self.assertIn('role() == "technician"', RULES)
        self.assertIn('role() == "technicians"', RULES)
        self.assertIn('rawRole === "technicians" ? "technician" : rawRole', TICKETS_JS)
        self.assertIn('"admin", "pi", "technician"', TICKETS_JS)

    def test_technician_updates_are_field_limited(self) -> None:
        self.assertIn("technicianUpdateFieldsOnly", RULES)
        self.assertIn("affectedKeys().hasOnly", RULES)
        self.assertIn("allow delete: if isAdmin();", RULES)


if __name__ == "__main__":
    unittest.main()
