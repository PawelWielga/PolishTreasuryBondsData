import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.sources import SourceError
from scripts.update import _validate_official_mf_workbook_url, sync_mf


class LocalMinistryWorkbookProvenanceTests(unittest.TestCase):
    OFFICIAL_URL = "https://www.gov.pl/attachment/f69d7165-7300-4377-be6a-13d0cafd778e"

    def test_exact_official_attachment_url_is_accepted(self) -> None:
        self.assertEqual(
            self.OFFICIAL_URL,
            _validate_official_mf_workbook_url(self.OFFICIAL_URL),
        )

    def test_landing_page_cannot_masquerade_as_workbook_provenance(self) -> None:
        with self.assertRaisesRegex(SourceError, "exact official"):
            _validate_official_mf_workbook_url(
                "https://www.gov.pl/web/finanse/obligacje-detaliczne1"
            )

    def test_lookalike_or_non_https_host_is_rejected(self) -> None:
        for url in (
            "http://www.gov.pl/attachment/example",
            "https://www.gov.pl.evil.example/attachment/example",
            "https://gov.pl/attachment/example",
        ):
            with self.subTest(url=url):
                with self.assertRaisesRegex(SourceError, "exact official"):
                    _validate_official_mf_workbook_url(url)

    def test_attachment_url_with_query_or_fragment_is_rejected(self) -> None:
        for suffix in ("?download=1", "#fragment"):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(SourceError, "exact official"):
                    _validate_official_mf_workbook_url(self.OFFICIAL_URL + suffix)

    def test_local_workbook_requires_explicit_official_provenance_url(self) -> None:
        with self.assertRaisesRegex(SourceError, "--mf-workbook requires --mf-workbook-url"):
            sync_mf(object(), "2026-09-05", Path("does-not-need-to-exist.xls"), False)

    def test_provenance_url_without_local_workbook_is_rejected(self) -> None:
        with self.assertRaisesRegex(SourceError, "only be used together"):
            sync_mf(
                object(),
                "2026-09-05",
                workbook_url=self.OFFICIAL_URL,
                cross_check=False,
            )

    def test_live_discovery_rejects_external_workbook_host_before_fetch(self) -> None:
        page = b'''<a class="file-download"
            href="https://evil.example/attachment/fake.xls"
            aria-label="Dane dotyczace obligacji detalicznych plik w formacie xls">
            Pobierz
        </a>'''
        with patch("scripts.update.fetch", return_value=page) as fetch_mock:
            with self.assertRaisesRegex(SourceError, "exact official"):
                sync_mf(object(), "2026-09-05", cross_check=False)

        fetch_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
