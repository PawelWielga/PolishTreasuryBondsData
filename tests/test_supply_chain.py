from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHA_REF_RE = re.compile(r"[0-9a-f]{40}")
DIRECT_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s]+)$")
LOCKED_REQUIREMENT_RE = re.compile(
    r"(?m)^([A-Za-z0-9_.-]+)==([^\s\\]+)\s+\\\n"
    r"\s+--hash=sha256:([0-9a-f]{64})$"
)
ACTION_USE_RE = re.compile(r"uses:\s*([^@\s]+)@([^\s#]+)")


def normalize_package_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


class SupplyChainTests(unittest.TestCase):
    def test_direct_requirements_are_present_in_hash_locked_file(self) -> None:
        direct: dict[str, str] = {}
        for raw_line in (ROOT / "requirements.in").read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = DIRECT_REQUIREMENT_RE.fullmatch(line)
            self.assertIsNotNone(match, f"Unpinned direct requirement: {line}")
            assert match is not None
            direct[normalize_package_name(match.group(1))] = match.group(2)

        locked_text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        locked = {
            normalize_package_name(name): (version, digest)
            for name, version, digest in LOCKED_REQUIREMENT_RE.findall(locked_text)
        }

        self.assertGreater(len(locked), len(direct), "Lock file must include transitive dependencies")
        for package, expected_version in direct.items():
            self.assertIn(package, locked, f"Direct dependency {package} is missing from the lock")
            actual_version, digest = locked[package]
            self.assertEqual(expected_version, actual_version)
            self.assertEqual(64, len(digest))

    def test_all_external_github_actions_are_pinned_to_commit_sha(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        failures: list[str] = []
        for workflow in sorted(workflow_dir.glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            for action, ref in ACTION_USE_RE.findall(text):
                if action.startswith("./") or action.startswith("docker://"):
                    continue
                if SHA_REF_RE.fullmatch(ref) is None:
                    failures.append(f"{workflow.name}: {action}@{ref}")

        self.assertEqual([], failures, "External actions must use immutable 40-character commit SHAs")

    def test_network_sensitive_jobs_have_timeouts(self) -> None:
        for workflow_name in ("pages.yml", "update-data.yml"):
            text = (ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8")
            self.assertIn("timeout-minutes:", text, f"{workflow_name} must have a job timeout")


if __name__ == "__main__":
    unittest.main()
