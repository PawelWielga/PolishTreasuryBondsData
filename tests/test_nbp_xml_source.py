import unittest
from unittest.mock import call, patch

from scripts.sources import NBP_RATES_URL, SourceError, parse_nbp_rates
from scripts.update import NBP_CURRENT_RATES_URL, NBP_HISTORY_START, sync_nbp


class NbpXmlSourceTests(unittest.TestCase):
    def test_uses_official_machine_readable_sources(self):
        self.assertEqual(
            "https://static.nbp.pl/dane/stopy/stopy_procentowe_archiwum.xml",
            NBP_RATES_URL,
        )
        self.assertEqual(
            "https://static.nbp.pl/dane/stopy/stopy_procentowe.xml",
            NBP_CURRENT_RATES_URL,
        )

    def test_parses_official_nested_archive_shape(self):
        xml = """
        <stopy_procentowe_archiwum>
          <pozycje obowiazuje_od="2025-12-04">
            <pozycja id="ref" oprocentowanie="4,00" />
          </pozycje>
          <pozycje obowiazuje_od="2026-03-05">
            <pozycja id="ref" oprocentowanie="3,75" />
          </pozycje>
        </stopy_procentowe_archiwum>
        """

        rates = parse_nbp_rates(xml)

        self.assertEqual(
            [("2025-12-04", "4.00"), ("2026-03-05", "3.75")],
            [(item["effectiveFrom"], item["annualRatePercent"]) for item in rates],
        )

    def test_parses_official_current_rate_shape(self):
        xml = """
        <stopy_procentowe>
          <tabela id="stoproc">
            <pozycja id="ref" oprocentowanie="3,75" obowiazuje_od="2026-03-05" />
          </tabela>
        </stopy_procentowe>
        """

        rates = parse_nbp_rates(xml)

        self.assertEqual(
            [("2026-03-05", "3.75")],
            [(item["effectiveFrom"], item["annualRatePercent"]) for item in rates],
        )

    def test_sync_cross_checks_archive_and_current_without_backfilling_pre_ror_history(self):
        archive_xml = """
        <stopy_procentowe_archiwum>
          <pozycje obowiazuje_od="1998-02-26">
            <pozycja id="ref" oprocentowanie="24,00" />
          </pozycje>
          <pozycje obowiazuje_od="2022-05-06">
            <pozycja id="ref" oprocentowanie="5,25" />
          </pozycje>
          <pozycje obowiazuje_od="2026-03-05">
            <pozycja id="ref" oprocentowanie="3,75" />
          </pozycje>
        </stopy_procentowe_archiwum>
        """
        current_xml = """
        <stopy_procentowe>
          <tabela id="stoproc">
            <pozycja id="ref" oprocentowanie="3,75" obowiazuje_od="2026-03-05" />
          </tabela>
        </stopy_procentowe>
        """
        existing = [
            {
                "effectiveFrom": "2022-05-06",
                "annualRatePercent": "5.25",
                "revision": 1,
                "publishedAt": None,
                "source": NBP_RATES_URL,
            },
            {
                "effectiveFrom": "2026-03-05",
                "annualRatePercent": "3.75",
                "revision": 1,
                "publishedAt": None,
                "source": NBP_RATES_URL,
            },
        ]
        source = {"observations": existing}
        session = object()

        with (
            patch("scripts.update.load_json", return_value=source),
            patch(
                "scripts.update.fetch",
                side_effect=[archive_xml.encode("utf-8"), current_xml.encode("utf-8")],
            ) as fetch_mock,
            patch("scripts.update.write_json") as write_mock,
        ):
            self.assertEqual(0, sync_nbp(session, "2026-09-04T00:00:00Z"))

        self.assertEqual(
            [
                call(session, NBP_RATES_URL, "application/xml,text/xml"),
                call(session, NBP_CURRENT_RATES_URL, "application/xml,text/xml"),
            ],
            fetch_mock.call_args_list,
        )
        write_mock.assert_not_called()
        self.assertEqual("2022-05-06", NBP_HISTORY_START)

    def test_sync_publishes_current_rate_when_it_is_ahead_of_archive(self):
        archive_xml = """
        <stopy_procentowe_archiwum>
          <pozycje obowiazuje_od="1998-02-26">
            <pozycja id="ref" oprocentowanie="24,00" />
          </pozycje>
          <pozycje obowiazuje_od="2022-05-06">
            <pozycja id="ref" oprocentowanie="5,25" />
          </pozycje>
          <pozycje obowiazuje_od="2026-03-05">
            <pozycja id="ref" oprocentowanie="3,75" />
          </pozycje>
        </stopy_procentowe_archiwum>
        """
        current_xml = """
        <stopy_procentowe>
          <tabela id="stoproc">
            <pozycja id="ref" oprocentowanie="3,50" obowiazuje_od="2026-09-04" />
          </tabela>
        </stopy_procentowe>
        """
        source = {
            "observations": [
                {
                    "effectiveFrom": "2022-05-06",
                    "annualRatePercent": "5.25",
                    "revision": 1,
                    "publishedAt": None,
                    "source": NBP_RATES_URL,
                },
                {
                    "effectiveFrom": "2026-03-05",
                    "annualRatePercent": "3.75",
                    "revision": 1,
                    "publishedAt": None,
                    "source": NBP_RATES_URL,
                },
            ]
        }
        session = object()

        with (
            patch("scripts.update.load_json", return_value=source),
            patch(
                "scripts.update.fetch",
                side_effect=[archive_xml.encode("utf-8"), current_xml.encode("utf-8")],
            ),
            patch("scripts.update.write_json") as write_mock,
        ):
            self.assertEqual(1, sync_nbp(session, "2026-09-04T00:00:00Z"))

        written = write_mock.call_args.args[1]
        dates = [item["effectiveFrom"] for item in written["observations"]]
        self.assertEqual(["2022-05-06", "2026-03-05", "2026-09-04"], dates)
        self.assertNotIn("1998-02-26", dates)
        self.assertEqual(NBP_CURRENT_RATES_URL, written["observations"][-1]["source"])
        self.assertEqual(NBP_RATES_URL, written["source"]["url"])
        self.assertEqual(NBP_CURRENT_RATES_URL, written["source"]["currentUrl"])

    def test_sync_fails_closed_when_current_and_archive_disagree(self):
        archive_xml = """
        <stopy_procentowe_archiwum>
          <pozycje obowiazuje_od="2022-05-06">
            <pozycja id="ref" oprocentowanie="5,25" />
          </pozycje>
          <pozycje obowiazuje_od="2026-03-05">
            <pozycja id="ref" oprocentowanie="3,75" />
          </pozycje>
        </stopy_procentowe_archiwum>
        """
        current_xml = """
        <stopy_procentowe>
          <tabela id="stoproc">
            <pozycja id="ref" oprocentowanie="9,99" obowiazuje_od="2026-03-05" />
          </tabela>
        </stopy_procentowe>
        """
        source = {"observations": []}

        with (
            patch("scripts.update.load_json", return_value=source),
            patch(
                "scripts.update.fetch",
                side_effect=[archive_xml.encode("utf-8"), current_xml.encode("utf-8")],
            ),
            patch("scripts.update.write_json") as write_mock,
        ):
            with self.assertRaisesRegex(SourceError, "disagree"):
                sync_nbp(object(), "2026-09-04T00:00:00Z")

        write_mock.assert_not_called()

    def test_sync_fails_closed_when_current_file_is_older_than_archive(self):
        archive_xml = """
        <stopy_procentowe_archiwum>
          <pozycje obowiazuje_od="2022-05-06">
            <pozycja id="ref" oprocentowanie="5,25" />
          </pozycje>
          <pozycje obowiazuje_od="2026-09-04">
            <pozycja id="ref" oprocentowanie="3,50" />
          </pozycje>
        </stopy_procentowe_archiwum>
        """
        current_xml = """
        <stopy_procentowe>
          <tabela id="stoproc">
            <pozycja id="ref" oprocentowanie="3,75" obowiazuje_od="2026-03-05" />
          </tabela>
        </stopy_procentowe>
        """

        with (
            patch("scripts.update.load_json", return_value={"observations": []}),
            patch(
                "scripts.update.fetch",
                side_effect=[archive_xml.encode("utf-8"), current_xml.encode("utf-8")],
            ),
            patch("scripts.update.write_json") as write_mock,
        ):
            with self.assertRaisesRegex(SourceError, "older than the archive"):
                sync_nbp(object(), "2026-09-04T00:00:00Z")

        write_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
