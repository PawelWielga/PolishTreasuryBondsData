from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import smoke_pages


class FakeResponse:
    def __init__(self, content: bytes, status_code: int, url: str) -> None:
        self.content = content
        self.status_code = status_code
        self.url = url


class FakeSession:
    def __init__(self, resources: dict[str, bytes]) -> None:
        self.resources = resources

    def get(self, url: str, **_kwargs) -> FakeResponse:
        if url not in self.resources:
            return FakeResponse(b"", 404, url)
        return FakeResponse(self.resources[url], 200, url)


class PublicSchemaSmokeTests(unittest.TestCase):
    def make_schema(self, root: Path, base: str, name: str = "catalog-v2.schema.json") -> bytes:
        content = (
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": f"{base}schemas/{name}",
                    "type": "object",
                },
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
        (root / name).write_bytes(content)
        return content

    def test_reviewed_schema_must_be_public_byte_for_byte(self) -> None:
        base = "https://example.test/project/"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            content = self.make_schema(root, base)
            session = FakeSession({f"{base}schemas/catalog-v2.schema.json": content})

            smoke_pages.verify_public_schemas(session, base, root, 1, 0)

    def test_missing_public_schema_fails(self) -> None:
        base = "https://example.test/project/"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_schema(root, base)

            with self.assertRaisesRegex(smoke_pages.SmokeError, "Could not fetch deployed Pages resource"):
                smoke_pages.verify_public_schemas(FakeSession({}), base, root, 1, 0)

    def test_public_schema_with_different_bytes_fails(self) -> None:
        base = "https://example.test/project/"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_schema(root, base)
            session = FakeSession(
                {f"{base}schemas/catalog-v2.schema.json": b'{"$id":"different"}\n'}
            )

            with self.assertRaisesRegex(smoke_pages.SmokeError, "bytes do not match"):
                smoke_pages.verify_public_schemas(session, base, root, 1, 0)

    def test_schema_id_must_match_its_deployed_url(self) -> None:
        base = "https://example.test/project/"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "catalog-v2.schema.json"
            content = b'{"$id":"https://wrong.example/schema.json"}\n'
            path.write_bytes(content)
            session = FakeSession({f"{base}schemas/catalog-v2.schema.json": content})

            with self.assertRaisesRegex(smoke_pages.SmokeError, "\$id mismatch"):
                smoke_pages.verify_public_schemas(session, base, root, 1, 0)

    def test_pages_workflow_uploads_publication_and_schemas_as_one_site(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pages.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("cp -a publication/. _site/", workflow)
        self.assertIn("cp -a schemas/*.json _site/schemas/", workflow)
        self.assertIn("path: _site", workflow)


if __name__ == "__main__":
    unittest.main()
