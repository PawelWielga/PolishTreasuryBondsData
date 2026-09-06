from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BotValidationDispatchTests(unittest.TestCase):
    def test_updater_can_dispatch_required_validation_after_bot_pr_write(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "update-data.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("actions: write", workflow)
        self.assertIn("id: create_pr", workflow)
        self.assertIn("steps.create_pr.outputs.pull-request-number != ''", workflow)
        self.assertIn("gh workflow run validate.yml --ref bot/update-data", workflow)

        create_position = workflow.index("id: create_pr")
        dispatch_position = workflow.index("gh workflow run validate.yml --ref bot/update-data")
        self.assertLess(create_position, dispatch_position)

    def test_manual_validation_cannot_skip_immutable_archive_guard(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)
        self.assertIn("git merge-base HEAD origin/main", workflow)
        self.assertIn(
            'python scripts/check_immutable_snapshots.py --base "$reviewed_base"',
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
