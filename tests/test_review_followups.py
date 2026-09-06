from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import smoke_pages, update
from scripts.sources import SourceError


class _Response:
    def __init__(self, content: bytes, status_code: int, url: str) -> None:
        self.content = content
        self.status_code = status_code
        self.url = url


class _Session:
    def __init__(self, resources: dict[str, bytes]) -> None:
        self.resources = resources

    def get(self, url: str, **_kwargs) -> _Response:
        if url not in self.resources:
            return _Response(b"", 404, url)
        return _Response(self.resources[url], 200, url)


class ReviewFollowupTests(unittest.TestCase):
    def test_dirty_tree_precondition_failure_never_runs_rollback(self) -> None:
        with (
            patch.object(
                update,
                "_require_clean_managed_tree",
                side_effect=SourceError("dirty managed tree"),
            ),
            patch.object(update, "_rollback_managed_tree") as rollback,
            patch.object(update, "build_dist") as build_dist,
            patch("sys.argv", ["update.py"]),
        ):
            result = update.main()

        self.assertEqual(1, result)
        rollback.assert_not_called()
        build_dist.assert_not_called()

    def test_frozen_legacy_schema_id_is_valid_at_pages_alias(self) -> None:
        base = "https://example.test/project/"
        name = "catalog-v1.schema.json"
        legacy_id = smoke_pages.LEGACY_SCHEMA_IDS[name]
        content = (
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": legacy_id,
                    "type": "object",
                },
                indent=2,
            )
            + "\n"
        ).encode("utf-8")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / name).write_bytes(content)
            alias = f"{base}schemas/{name}"
            smoke_pages.verify_public_schemas(
                _Session({alias: content}),
                base,
                root,
                attempts=1,
                retry_delay_seconds=0,
            )

    def test_nonlegacy_schema_still_rejects_non_pages_id(self) -> None:
        base = "https://example.test/project/"
        name = "catalog-v2.schema.json"
        content = b'{"$id":"https://wrong.example/schema.json"}\n'

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / name).write_bytes(content)
            alias = f"{base}schemas/{name}"
            with self.assertRaisesRegex(smoke_pages.SmokeError, r"\$id mismatch"):
                smoke_pages.verify_public_schemas(
                    _Session({alias: content}),
                    base,
                    root,
                    attempts=1,
                    retry_delay_seconds=0,
                )


if __name__ == "__main__":
    unittest.main()
