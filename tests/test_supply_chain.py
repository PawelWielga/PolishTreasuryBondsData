from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHA_REF_RE = re.compile(r"[0-9a-f]{40}")
DIRECT_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s]+)$")
LOCKED_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)(?:\s+\\)?$")
HASH_RE = re.compile(r"^--hash=sha256:([0-9a-f]{64})(?:\s+\\)?$")
ACTION_USE_RE = re.compile(r"uses:\s*([^@\s]+)@([^\s#]+)")


def normalize_package_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def parse_hash_locked_requirements(text: str) -> dict[str, tuple[str, tuple[str, ...]]]:
    locked: dict[str, tuple[str, tuple[str, ...]]] = {}
    current_name: str | None = None
    current_version: str | None = None
    current_hashes: list[str] = []

    def finish_current() -> None:
        nonlocal current_name, current_version, current_hashes
        if current_name is None or current_version is None:
            return
        if not current_hashes:
            raise AssertionError(f"Locked dependency {current_name}=={current_version} has no SHA-256 hash")
        normalized = normalize_package_name(current_name)
        if normalized in locked:
            raise AssertionError(f"Locked dependency {current_name} is declared more than once")
        locked[normalized] = (current_version, tuple(current_hashes))
        current_name = None
        current_version = None
        current_hashes = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        requirement = LOCKED_REQUIREMENT_RE.fullmatch(line)
        if requirement is not None:
            finish_current()
            current_name, current_version = requirement.groups()
            continue

        digest = HASH_RE.fullmatch(line)
        if digest is not None:
            if current_name is None:
                raise AssertionError(f"Orphan hash line in requirements.txt: {line}")
            current_hashes.append(digest.group(1))
            continue

        raise AssertionError(f"Unsupported or unhashed lock-file line: {line}")

    finish_current()
    return locked


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
        locked = parse_hash_locked_requirements(locked_text)

        self.assertGreater(len(locked), len(direct), "Lock file must include transitive dependencies")
        for package, expected_version in direct.items():
            self.assertIn(package, locked, f"Direct dependency {package} is missing from the lock")
            actual_version, digests = locked[package]
            self.assertEqual(expected_version, actual_version)
            self.assertGreaterEqual(len(digests), 1)
            self.assertTrue(all(len(digest) == 64 for digest in digests))

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
