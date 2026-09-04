import unittest
from datetime import date
from unittest.mock import patch

from scripts import pipeline, update
from scripts.sources import SourceError, fetch_gus_history
from scripts.update import _validated_gus_observations, _validated_mf_series, sync_gus


class GusCoverageRegressionTests(unittest.TestCase):
    @staticmethod
    def _observation(period: str) -> dict:
        year, month = (int(part) for part in period.split("-"))
        return {
            "period": period,
            "indexPreviousYear100": "100.50",
            "yearOverYearPercent": "0.50",
            "revision": 1,
            "source": {
                "publisher": "GUS",
                "api": "SDP",
                "year": year,
                "periodId": 246 + month,
            },
        }

    @classmethod
    def _periods(cls, year: int, through_month: int) -> list[dict]:
        return [cls._observation(f"{year:04d}-{month:02d}") for month in range(1, through_month + 1)]

    def test_variable_api_year_is_not_declared_complete_only_because_calendar_advanced(self):
        with (
            patch("scripts.sources.fetch", return_value=b""),
            patch("scripts.sources.parse_gus_variable_responses", return_value=[]) as parser,
        ):
            fetch_gus_history(object(), 2026, 2026, sleep=lambda _: None)

        parser.assert_called_once_with([], 2026, require_complete=False)

    def test_previous_year_may_remain_partial_until_new_year_observation_exists(self):
        incoming = self._periods(2026, 11)

        validated = _validated_gus_observations(incoming, incoming, 2026, 2027)

        self.assertEqual(incoming, validated)

    def test_previous_year_must_be_complete_once_new_year_observation_exists(self):
        incoming = self._periods(2026, 11) + self._periods(2027, 1)

        with self.assertRaisesRegex(SourceError, "incomplete/non-contiguous for 2026"):
            _validated_gus_observations(incoming, self._periods(2026, 11), 2026, 2027)

    def test_live_refresh_rejects_loss_of_previously_published_period(self):
        existing = self._periods(2026, 12)
        truncated = self._periods(2026, 11)
        source = {"observations": existing}

        with (
            patch("scripts.update.load_json", return_value=source),
            patch("scripts.update.fetch_gus_history", return_value=truncated),
            patch("scripts.update.write_json") as write_mock,
        ):
            with self.assertRaisesRegex(SourceError, "lost previously published.*2026-12"):
                sync_gus(object(), 2026, 2027, "2027-01-05T00:00:00Z")

        write_mock.assert_not_called()

    def test_offline_validation_allows_year_rollover_before_new_year_data(self):
        source = {
            "verifiedAt": "2015-01-05T00:00:00Z",
            "observations": self._periods(2014, 11),
        }

        pipeline._validate_gus(source)

    def test_offline_validation_requires_previous_year_complete_after_new_year_data(self):
        source = {
            "verifiedAt": "2015-01-20T00:00:00Z",
            "observations": self._periods(2014, 11) + self._periods(2015, 1),
        }

        with self.assertRaisesRegex(ValueError, "incomplete/non-contiguous for 2014"):
            pipeline._validate_gus(source)

    def test_live_and_offline_gus_contract_boundaries_cannot_drift(self):
        self.assertEqual(update.GUS_HISTORY_START, pipeline.GUS_HISTORY_START)


class MinistryCoverageRegressionTests(unittest.TestCase):
    @staticmethod
    def _series(code: str, product_type: str, sale_to: str, revision: int = 1) -> dict:
        return {
            "seriesCode": code,
            "productType": product_type,
            "saleTo": sale_to,
            "termsRevision": revision,
        }

    def test_missing_previously_published_outstanding_series_fails_closed(self):
        existing = [self._series("ROR0927", "ROR", "2026-09-30")]

        with self.assertRaisesRegex(SourceError, "lost previously published outstanding series.*ROR0927"):
            _validated_mf_series([], existing, date(2026, 9, 4))

    def test_matured_series_may_disappear_from_current_workbook_scope(self):
        existing = [self._series("OTS0120", "OTS", "2020-01-31")]

        self.assertEqual([], _validated_mf_series([], existing, date(2026, 9, 4)))

    def test_mf_workbook_duplicate_series_codes_fail_closed(self):
        parsed = [
            self._series("ROR0927", "ROR", "2026-09-30"),
            self._series("ROR0927", "ROR", "2026-09-30"),
        ]

        with self.assertRaisesRegex(SourceError, "duplicate supported series codes"):
            _validated_mf_series(parsed, [], date(2026, 9, 4))


if __name__ == "__main__":
    unittest.main()
