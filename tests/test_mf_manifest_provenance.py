import unittest

from scripts import pipeline


class MinistryManifestProvenanceTests(unittest.TestCase):
    @staticmethod
    def series(
        code: str,
        sale_from: str,
        verified_at: str,
        workbook_sha: str,
        workbook_url: str,
        revision: int = 1,
    ) -> dict:
        return {
            "seriesCode": code,
            "saleFrom": sale_from,
            "termsRevision": revision,
            "provenance": {
                "verifiedAt": verified_at,
                "primary": {
                    "publisher": "Ministry of Finance",
                    "url": workbook_url,
                    "sha256": workbook_sha,
                },
            },
        }

    def test_manifest_fields_come_from_same_most_recently_verified_source(self) -> None:
        older_latest_series = self.series(
            "EDO0936",
            "2026-09-01",
            "2026-09-03",
            "a" * 64,
            "https://example.test/old.xls",
        )
        corrected_older_series = self.series(
            "ROR0827",
            "2026-08-01",
            "2026-09-05",
            "b" * 64,
            "https://example.test/new.xls",
            revision=2,
        )

        provenance = pipeline._mf_provenance(
            [corrected_older_series, older_latest_series]
        )

        self.assertEqual("2026-09-05", provenance["verifiedAt"])
        self.assertEqual("b" * 64, provenance["sha256"])
        self.assertEqual("https://example.test/new.xls", provenance["url"])

    def test_same_day_selection_is_deterministic(self) -> None:
        first = self.series(
            "ROR0927",
            "2026-09-01",
            "2026-09-05",
            "a" * 64,
            "https://example.test/a.xls",
        )
        second = self.series(
            "EDO0936",
            "2026-09-01",
            "2026-09-05",
            "b" * 64,
            "https://example.test/b.xls",
        )

        expected = pipeline._mf_provenance([first, second])
        actual = pipeline._mf_provenance([second, first])

        self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
