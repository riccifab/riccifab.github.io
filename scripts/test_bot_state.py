import json
import tempfile
import unittest
from pathlib import Path

from merge_bot_state import merge_notified_ticket_ids


class BotStateMergeTest(unittest.TestCase):
    def test_notified_ticket_ids_are_merged_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            (source / "notified_ticket_ids.json").write_text(
                json.dumps({"ticket_ids": ["ticket-b", "ticket-a"]}),
                encoding="utf-8",
            )
            (target / "notified_ticket_ids.json").write_text(
                json.dumps({"ticket_ids": ["ticket-c", "ticket-b"]}),
                encoding="utf-8",
            )

            merge_notified_ticket_ids(source, target)

            merged = json.loads((target / "notified_ticket_ids.json").read_text(encoding="utf-8"))
            self.assertEqual(merged["ticket_ids"], ["ticket-a", "ticket-b", "ticket-c"])


if __name__ == "__main__":
    unittest.main()
