import json
import unittest
from pathlib import Path

from scripts.pipeline import DATA, build_dist, load_series
from scripts.sources import (
    MF_PAGE_URL,
    SourceError,
    cross_check_series,
    discover_mf_workbook,
    parse_gus_indicator_response,
    parse_gus_variable_responses,
    parse_mf_workbook,
    parse_nbp_rates,
    parse_series_html,
    terms_content_hash,
)
from scripts.update import _merge_revisions, transition_source_status

ROOT = Path(__file__).resolve().parents[1]


class MinistryOfFinanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workbook = next((DATA / "sources" / "mf").glob("*.xls"))
        cls.series = parse_mf_workbook(cls.workbook.read_bytes(), MF_PAGE_URL, "2026-09-03")

    def test_discovers_single_official_xls_attachment(self):
        html = '<a class="file-download" href="/attachment/official" aria-label="Dane dotyczące obligacji detalicznych plik w formacie xls">Pobierz</a>'
        self.assertEqual("https://www.gov.pl/attachment/official", discover_mf_workbook(html))

    def test_workbook_fixture_contains_all_supported_families(self):
        self.assertEqual({"OTS", "ROR", "DOR", "TOS", "COI", "EDO", "ROS", "ROD"}, {x["productType"] for x in self.series})

    def test_parses_exact_current_edo_facts(self):
        edo = next(x for x in self.series if x["seriesCode"] == "EDO0936")
        self.assertEqual("PL0000119483", edo["isin"])
        self.assertEqual(10000, edo["issuePriceMinorUnits"])
        self.assertEqual(9990, edo["exchangePriceMinorUnits"])
        self.assertEqual("5.35", edo["firstPeriodAnnualRatePercent"])
        self.assertEqual("2.00", edo["marginPercent"])
        self.assertRegex(edo["contentHash"], r"^sha256:[a-f0-9]{64}$")

    def test_parses_ros_and_rod(self):
        ros = next(x for x in self.series if x["seriesCode"] == "ROS0932")
        rod = next(x for x in self.series if x["seriesCode"] == "ROD0938")
        self.assertEqual(("5.00", "2.00"), (ros["firstPeriodAnnualRatePercent"], ros["marginPercent"]))
        self.assertEqual(("5.60", "2.50"), (rod["firstPeriodAnnualRatePercent"], rod["marginPercent"]))

    def test_html_is_an_independent_cross_check(self):
        html = """
        <h1>10-letnie obligacje EDO</h1><p>Seria: EDO0936</p><p>Oprocentowanie: 5,35% w pierwszym okresie</p>
        <p>w kolejnych okresach: marża 2,00% + inflacja</p>
        <p>Sprzedaż: 01.09.2026 - 30.09.2026</p>
        <p>Cena sprzedaży jednej obligacji: 100,00 zł</p>
        """
        workbook = next(x for x in self.series if x["seriesCode"] == "EDO0936")
        cross_check_series(workbook, parse_series_html(html))
        facts = parse_series_html(html)
        facts["firstPeriodAnnualRatePercent"] = "9.99"
        with self.assertRaisesRegex(SourceError, "official sources disagree"):
            cross_check_series(workbook, facts)

    def test_provenance_does_not_change_financial_content_hash(self):
        item = dict(next(x for x in self.series if x["seriesCode"] == "EDO0936"))
        expected = item["contentHash"]
        item["provenance"] = {"changed": True}
        self.assertEqual(expected, terms_content_hash(item))


class ReferenceDataTests(unittest.TestCase):
    def test_parses_complete_legacy_gus_year_and_preserves_negative_cpi(self):
        payload = [
            {"id-daty": 2025, "id-okres": 247 + month, "wartosc": 99.5 if month == 0 else 102.9}
            for month in range(12)
        ]
        observations = parse_gus_indicator_response(payload, 2025)
        self.assertEqual("-0.50", observations[0]["yearOverYearPercent"])
        self.assertEqual("2025-12", observations[-1]["period"])
        self.assertNotIn("appliesToInterestPeriodStartMonth", observations[0])

    def test_parses_2026_variable_shape(self):
        payload = {"data": [
            {"id-daty": 2026, "id-okres": 247, "id-pozycja-2": 123, "id-pozycja-3": 456, "id-sposob-prezentacji-miara": 5, "wartosc": 999},
            {"id-daty": 2026, "id-okres": 247, "id-pozycja-2": 14916914, "id-pozycja-3": 6902025, "id-sposob-prezentacji-miara": 5, "wartosc": 102.1}
        ]}
        self.assertEqual("2.10", parse_gus_variable_responses([payload], 2026)[0]["yearOverYearPercent"])

    def test_parses_nbp_reference_rate_timeline_fixture(self):
        xml = (ROOT / "tests" / "fixtures" / "nbp" / "rates.xml").read_text(encoding="utf-8")
        rates = parse_nbp_rates(xml)
        self.assertEqual(("2022-05-06", "5.25"), (rates[0]["effectiveFrom"], rates[0]["annualRatePercent"]))
        self.assertEqual(("2026-03-05", "3.75"), (rates[-1]["effectiveFrom"], rates[-1]["annualRatePercent"]))
        self.assertNotIn("appliesToInterestPeriodStartMonth", rates[0])

    def test_correction_creates_a_new_observation_revision(self):
        old = [{"period": "2025-01", "indexPreviousYear100": "102.00", "revision": 1}]
        new = [{"period": "2025-01", "indexPreviousYear100": "102.10", "revision": 1}]
        merged = _merge_revisions(old, new, "period", "indexPreviousYear100")
        self.assertEqual([1, 2], [item["revision"] for item in merged])


class PublicationTests(unittest.TestCase):
    def test_per_series_sources_have_canonical_aggregate_order(self):
        series = load_series()
        self.assertGreater(len(series), 400)
        self.assertEqual(series, sorted(series, key=lambda x: (x["saleFrom"], x["seriesCode"], x["termsRevision"])))
        self.assertEqual(len(series), len({(x["seriesCode"], x["termsRevision"]) for x in series}))

    def test_offline_build_is_idempotent_and_includes_coverage(self):
        first = build_dist()
        second = build_dist()
        self.assertEqual(first, second)
        manifest = json.loads((ROOT / "publication" / "v1" / "snapshots" / first / "manifest.json").read_text())
        self.assertEqual(8, len(manifest["coverage"]["catalog"]))
        self.assertEqual("2014-01", manifest["coverage"]["gusCpi"]["fromPeriod"])

    def test_failed_refresh_does_not_advance_last_success(self):
        item = {"status":"FRESH", "lastSuccessAt":"2026-09-03T00:00:00Z"}
        transition_source_status(item, False, "2026-09-04T00:00:00Z", "upstream failed")
        self.assertEqual("STALE", item["status"])
        self.assertEqual("FAILED", item["lastAttemptStatus"])
        self.assertEqual("2026-09-03T00:00:00Z", item["lastSuccessAt"])


if __name__ == "__main__":
    unittest.main()
