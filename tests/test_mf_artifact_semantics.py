from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from scripts import pipeline
from scripts.sources import parse_mf_workbook, terms_financial_view


ROOT = Path(__file__).resolve().parents[1]


class MinistryArtifactSemanticIntegrityTests(unittest.TestCase):
    def test_every_terms_revision_matches_its_referenced_official_workbook_row(self) -> None:
        parsed_by_source: dict[tuple[str, str, str], dict[str, dict]] = {}
        checked = 0

        for terms_path in sorted((ROOT / "data" / "series").glob("*/*/terms-v*.json")):
            terms = json.loads(terms_path.read_text(encoding="utf-8"))
            provenance = terms["provenance"]
            primary = provenance["primary"]
            digest = primary["sha256"]
            artifact = ROOT / "data" / "sources" / "mf" / f"{digest}.xls"

            self.assertTrue(artifact.is_file(), f"Missing MF artifact for {terms_path}: {artifact}")
            content = artifact.read_bytes()
            self.assertEqual(
                digest,
                hashlib.sha256(content).hexdigest(),
                f"MF artifact hash mismatch for {terms_path}",
            )

            cache_key = (digest, primary["url"], provenance["verifiedAt"])
            if cache_key not in parsed_by_source:
                parsed = parse_mf_workbook(content, primary["url"], provenance["verifiedAt"])
                parsed_by_source[cache_key] = {item["seriesCode"]: item for item in parsed}

            parsed_by_code = parsed_by_source[cache_key]
            self.assertIn(
                terms["seriesCode"],
                parsed_by_code,
                f"{terms_path} points at an MF workbook that does not contain the series",
            )
            reconstructed = parsed_by_code[terms["seriesCode"]]

            self.assertEqual(
                primary["sheet"],
                reconstructed["provenance"]["primary"]["sheet"],
                f"MF provenance sheet mismatch for {terms_path}",
            )
            self.assertEqual(
                primary["row"],
                reconstructed["provenance"]["primary"]["row"],
                f"MF provenance row mismatch for {terms_path}",
            )
            self.assertEqual(
                terms_financial_view(terms),
                terms_financial_view(reconstructed),
                f"Financial terms do not match the referenced MF workbook row for {terms_path}",
            )
            checked += 1

        self.assertGreater(checked, 0, "No MF terms revisions were checked")


if __name__ == "__main__":
    unittest.main()
