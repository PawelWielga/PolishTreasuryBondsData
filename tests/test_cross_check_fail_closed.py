import copy
import unittest

from scripts import pipeline
from scripts.sources import SourceError, parse_series_html
from scripts.update import required_cross_check_fields, validate_cross_check_facts

CROSS_CHECK_URL = "https://www.obligacjeskarbowe.pl/oferta-obligacji/example/"


class CrossCheckCompletenessTests(unittest.TestCase):
    def test_variable_rate_products_explicitly_require_margin(self):
        for product_type in ("ROR", "DOR", "COI", "EDO", "ROS", "ROD"):
            with self.subTest(product_type=product_type):
                fields = required_cross_check_fields({
                    "seriesCode": f"{product_type}0000",
                    "productType": product_type,
                })
                self.assertIn("marginPercent", fields)

    def test_fixed_rate_products_do_not_require_margin(self):
        for product_type in ("OTS", "TOS"):
            with self.subTest(product_type=product_type):
                fields = required_cross_check_fields({
                    "seriesCode": f"{product_type}0000",
                    "productType": product_type,
                })
                self.assertNotIn("marginPercent", fields)

    def test_parser_wording_drift_for_variable_margin_fails_closed(self):
        html = """
        <main>
          <h1>10-letnie obligacje EDO</h1>
          <p>Seria: EDO0936</p>
          <p>Oprocentowanie: 5,35%</p>
          <p>W kolejnych okresach oprocentowanie uwzględnia inflację i 2,00 punktu procentowego premii.</p>
          <p>Sprzedaż: 01.09.2026 - 30.09.2026</p>
          <p>Cena sprzedaży jednej obligacji: 100,00 zł</p>
        </main>
        """
        facts = parse_series_html(html)
        self.assertIsNone(facts["marginPercent"])

        series = {"seriesCode": "EDO0936", "productType": "EDO"}
        with self.assertRaises(SourceError) as raised:
            validate_cross_check_facts(series, facts, CROSS_CHECK_URL)

        message = str(raised.exception)
        self.assertIn("EDO0936", message)
        self.assertIn("marginPercent", message)
        self.assertIn(CROSS_CHECK_URL, message)

    def test_fixed_rate_product_accepts_missing_irrelevant_margin(self):
        series = {"seriesCode": "TOS0929", "productType": "TOS"}
        facts = {
            "seriesCode": "TOS0929",
            "saleFrom": "2026-09-01",
            "saleTo": "2026-09-30",
            "issuePriceMinorUnits": 10000,
            "firstPeriodAnnualRatePercent": "4.40",
            "marginPercent": None,
            "maturityMonths": None,
        }
        validate_cross_check_facts(series, facts, CROSS_CHECK_URL)

    def test_missing_common_calculation_fact_fails_for_fixed_product_too(self):
        series = {"seriesCode": "TOS0929", "productType": "TOS"}
        facts = {
            "seriesCode": "TOS0929",
            "saleFrom": "2026-09-01",
            "saleTo": "2026-09-30",
            "issuePriceMinorUnits": None,
            "firstPeriodAnnualRatePercent": "4.40",
            "marginPercent": None,
        }
        with self.assertRaisesRegex(SourceError, "issuePriceMinorUnits"):
            validate_cross_check_facts(series, facts, CROSS_CHECK_URL)

    def test_maturity_heading_is_optional_cross_check_evidence(self):
        series = {"seriesCode": "ROR0927", "productType": "ROR"}
        facts = {
            "seriesCode": "ROR0927",
            "saleFrom": "2026-09-01",
            "saleTo": "2026-09-30",
            "issuePriceMinorUnits": 10000,
            "firstPeriodAnnualRatePercent": "4.00",
            "marginPercent": "0.00",
            "maturityMonths": None,
        }
        validate_cross_check_facts(series, facts, CROSS_CHECK_URL)

    def test_catalog_rejects_cross_check_without_verification_evidence(self):
        item = copy.deepcopy(
            next(
                series
                for series in pipeline.load_series()
                if series["provenance"].get("crossCheck") is not None
            )
        )
        item["provenance"]["crossCheck"].pop("verifiedAt", None)
        document = {
            "schemaVersion": "2.0",
            "generatedAt": "2026-09-03T00:00:00Z",
            "series": [item],
        }
        schema = pipeline.load_json(pipeline.SCHEMAS / "catalog-v2.schema.json")

        with self.assertRaisesRegex(ValueError, "verifiedAt.*required"):
            pipeline._validate_schema(document, schema, "catalog.json")


if __name__ == "__main__":
    unittest.main()
