import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.sources import NBP_RATES_URL, parse_nbp_rates
from scripts.update import sync_nbp

ROOT = Path(__file__).resolve().parents[1]


class NbpXmlSourceTests(unittest.TestCase):
    def test_uses_official_machine_readable_archive(self):
        self.assertEqual(
            "https://static.nbp.pl/dane/stopy/stopy_procentowe_archiwum.xml",
            NBP_RATES_URL,
        )

    def test_parses_official_nested_archive_shape(self):
        xml = """
        <stopy_procentowe_archiwum>
          <zmiana obowiazuje_od="2025-12-04">
            <pozycja id="ref" oprocentowanie="4,00" />
          </zmiana>
          <zmiana obowiazuje_od="2026-03-05">
            <pozycja id="ref" oprocentowanie="3,75" />
          </zmiana>
        </stopy_procentowe_archiwum>
        """

        rates = parse_nbp_rates(xml)

        self.assertEqual(
            [("2025-12-04", "4.00"), ("2026-03-05", "3.75")],
            [(item["effectiveFrom"], item["annualRatePercent"]) for item in rates],
        )

    def test_sync_requests_xml_instead_of_scraping_nbp_html(self):
        xml = (ROOT / "tests" / "fixtures" / "nbp" / "rates.xml").read_text(encoding="utf-8")
        source = {"observations": parse_nbp_rates(xml)}
        session = object()

        with (
            patch("scripts.update.load_json", return_value=source),
            patch("scripts.update.fetch", return_value=xml.encode("utf-8")) as fetch_mock,
            patch("scripts.update.write_json") as write_mock,
        ):
            self.assertEqual(0, sync_nbp(session, "2026-09-04T00:00:00Z"))

        fetch_mock.assert_called_once_with(session, NBP_RATES_URL, "application/xml,text/xml")
        write_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
