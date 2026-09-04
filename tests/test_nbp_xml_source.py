import unittest
from unittest.mock import call, patch

from scripts.sources import NBP_RATES_URL, SourceError, parse_nbp_rates
from scripts.update import NBP_CURRENT_RATES_URL, NBP_HISTORY_START, sync_nbp


class NbpXmlSourceTests(unittest.TestCase):
    @staticmethod
    def _archive(*rows: tuple[str, str]) -> str:
        body = "\n".join(
            f'''          <pozycje obowiazuje_od="{effective}">\n'''
            f'''            <pozycja id="ref" oprocentowanie="{rate}" />\n'''
            f'''          </pozycje>'''
            for effective, rate in rows
        )
        return f"<stopy_procentowe_archiwum>\n{body}\n</stopy_procentowe_archiwum>"

    @staticmethod
    def _current(effective: str, rate: str) -> str:
        return f'''<stopy_procentowe>
          <tabela id="stoproc">
            <pozycja id="ref" oprocentowanie="{rate}" obowiazuje_od="{effective}" />
          </tabela>
        </stopy_procentowe>'''

    @staticmethod
    def _observation(effective: str, rate: str) -> dict:
        return {
            "effectiveFrom": effective,
            "annualRatePercent": rate,
            "revision": 1,
            "publishedAt": None,
            "source": NBP_RATES_URL,
        }

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
        rates = parse_nbp_rates(
            self._archive(("2025-12-04", "4,00"), ("2026-03-05", "3,75"))
        )

        self.assertEqual(
            [("2025-12-04", "4.00"), ("2026-03-05", "3.75")],
            [(item["effectiveFrom"], item["annualRatePercent"]) for item in rates],
        )

    def test_parses_official_current_rate_shape(self):
        rates = parse_nbp_rates(self._current("2026-03-05", "3,75"))

        self.assertEqual(
            [("2026-03-05", "3.75")],
            [(item["effectiveFrom"], item["annualRatePercent"]) for item in rates],
        )

    def test_sync_cross_checks_sources_without_backfilling_pre_ror_history(self):
        archive_xml = self._archive(
            ("1998-02-26", "24,00"),
            ("2022-05-06", "5,25"),
            ("2026-03-05", "3,75"),
        )
        current_xml = self._current("2026-03-05", "3,75")
        source = {
            "source": {
                "publisher": "NBP",
                "url": NBP_RATES_URL,
                "currentUrl": NBP_CURRENT_RATES_URL,
                "verifiedAt": "2026-09-03T00:00:00Z",
            },
            "observations": [
                self._observation("2022-05-06", "5.25"),
                self._observation("2026-03-05", "3.75"),
            ],
        }
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

    def test_sync_migrates_source_provenance_without_financial_change(self):
        archive_xml = self._archive(
            ("2022-05-06", "5,25"),
            ("2026-03-05", "3,75"),
        )
        current_xml = self._current("2026-03-05", "3,75")
        source = {
            "source": {
                "publisher": "NBP",
                "url": "https://nbp.pl/podstawowe-stopy-procentowe-archiwum/",
                "verifiedAt": "2026-09-03T00:00:00Z",
            },
            "observations": [
                self._observation("2022-05-06", "5.25"),
                self._observation("2026-03-05", "3.75"),
            ],
        }

        with (
            patch("scripts.update.load_json", return_value=source),
            patch(
                "scripts.update.fetch",
                side_effect=[archive_xml.encode("utf-8"), current_xml.encode("utf-8")],
            ),
            patch("scripts.update.write_json") as write_mock,
        ):
            self.assertEqual(0, sync_nbp(object(), "2026-09-04T00:00:00Z"))

        written = write_mock.call_args.args[1]
        self.assertEqual(NBP_RATES_URL, written["source"]["url"])
        self.assertEqual(NBP_CURRENT_RATES_URL, written["source"]["currentUrl"])
        self.assertEqual("2026-09-04T00:00:00Z", written["verifiedAt"])

    def test_sync_fails_closed_when_current_file_is_newer_than_archive(self):
        archive_xml = self._archive(
            ("2022-05-06", "5,25"),
            ("2026-03-05", "3,75"),
        )
        current_xml = self._current("2026-09-04", "3,50")

        with (
            patch(
                "scripts.update.load_json",
                return_value={"observations": [self._observation("2022-05-06", "5.25")]},
            ),
            patch(
                "scripts.update.fetch",
                side_effect=[archive_xml.encode("utf-8"), current_xml.encode("utf-8")],
            ),
            patch("scripts.update.write_json") as write_mock,
        ):
            with self.assertRaisesRegex(SourceError, "not synchronized"):
                sync_nbp(object(), "2026-09-04T00:00:00Z")

        write_mock.assert_not_called()

    def test_sync_fails_closed_when_current_file_is_older_than_archive(self):
        archive_xml = self._archive(
            ("2022-05-06", "5,25"),
            ("2026-09-04", "3,50"),
        )
        current_xml = self._current("2026-03-05", "3,75")

        with (
            patch(
                "scripts.update.load_json",
                return_value={"observations": [self._observation("2022-05-06", "5.25")]},
            ),
            patch(
                "scripts.update.fetch",
                side_effect=[archive_xml.encode("utf-8"), current_xml.encode("utf-8")],
            ),
            patch("scripts.update.write_json") as write_mock,
        ):
            with self.assertRaisesRegex(SourceError, "not synchronized"):
                sync_nbp(object(), "2026-09-04T00:00:00Z")

        write_mock.assert_not_called()

    def test_sync_fails_closed_when_current_and_archive_disagree(self):
        archive_xml = self._archive(
            ("2022-05-06", "5,25"),
            ("2026-03-05", "3,75"),
        )
        current_xml = self._current("2026-03-05", "9,99")

        with (
            patch(
                "scripts.update.load_json",
                return_value={"observations": [self._observation("2022-05-06", "5.25")]},
            ),
            patch(
                "scripts.update.fetch",
                side_effect=[archive_xml.encode("utf-8"), current_xml.encode("utf-8")],
            ),
            patch("scripts.update.write_json") as write_mock,
        ):
            with self.assertRaisesRegex(SourceError, "disagree"):
                sync_nbp(object(), "2026-09-04T00:00:00Z")

        write_mock.assert_not_called()

    def test_sync_fails_closed_when_archive_loses_previously_published_date(self):
        archive_xml = self._archive(
            ("2022-05-06", "5,25"),
            ("2025-12-04", "4,00"),
            ("2026-03-05", "3,75"),
        )
        current_xml = self._current("2026-03-05", "3,75")
        source = {
            "observations": [
                self._observation("2022-05-06", "5.25"),
                self._observation("2022-06-09", "6.00"),
                self._observation("2025-12-04", "4.00"),
                self._observation("2026-03-05", "3.75"),
            ]
        }

        with (
            patch("scripts.update.load_json", return_value=source),
            patch(
                "scripts.update.fetch",
                side_effect=[archive_xml.encode("utf-8"), current_xml.encode("utf-8")],
            ),
            patch("scripts.update.write_json") as write_mock,
        ):
            with self.assertRaisesRegex(SourceError, "lost previously published.*2022-06-09"):
                sync_nbp(object(), "2026-09-04T00:00:00Z")

        write_mock.assert_not_called()

    def test_sync_fails_closed_when_archive_no_longer_starts_at_required_boundary(self):
        archive_xml = self._archive(
            ("2022-06-09", "6,00"),
            ("2026-03-05", "3,75"),
        )
        current_xml = self._current("2026-03-05", "3,75")

        with (
            patch("scripts.update.load_json", return_value={"observations": []}),
            patch(
                "scripts.update.fetch",
                side_effect=[archive_xml.encode("utf-8"), current_xml.encode("utf-8")],
            ),
            patch("scripts.update.write_json") as write_mock,
        ):
            with self.assertRaisesRegex(SourceError, "no longer covers required history"):
                sync_nbp(object(), "2026-09-04T00:00:00Z")

        write_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
