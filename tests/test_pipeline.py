import unittest

from scripts.pipeline import FAMILIES, discover_series_codes, parse_series_detail, semantically_equal


class PipelineTests(unittest.TestCase):
    def test_discovers_all_supported_current_families(self):
        html = """
        <div>Seria: OTS1226</div><div>Seria: ROR0927</div><div>Seria: DOR0928</div>
        <div>Seria: TOS0929</div><div>Seria: COI0930</div><div>Seria: EDO0936</div>
        """
        self.assertEqual(
            {
                "OTS": "OTS1226", "ROR": "ROR0927", "DOR": "DOR0928",
                "TOS": "TOS0929", "COI": "COI0930", "EDO": "EDO0936",
            },
            discover_series_codes(html),
        )

    def test_parses_ror_terms_from_official_page_shape(self):
        html = """
        <main>
          <p>Seria: ROR0927</p>
          <p>Oprocentowanie: 4,00% w skali roku, w pierwszym miesięcznym okresie odsetkowym.
          W kolejnych miesięcznych okresach odsetkowych: stopa referencyjna NBP+0,00%.</p>
          <p>Sprzedaż: 01.09.2026 - 30.09.2026</p>
          <p>Cena sprzedaży jednej obligacji: 100,00 zł</p>
        </main>
        """
        item = parse_series_detail(
            FAMILIES["ROR"], "ROR0927", html,
            "https://www.obligacjeskarbowe.pl/oferta-obligacji/obligacje-roczne-ror/ror0927/",
            "2026-09-03",
        )
        self.assertEqual(0.04, item["firstPeriodAnnualRate"])
        self.assertEqual("NbpReferencePlusMargin", item["rateRule"]["kind"])
        self.assertEqual(0.0, item["rateRule"]["margin"])
        self.assertEqual("2026-09-01", item["saleFrom"])
        self.assertTrue(item["termsVersion"].startswith("source-sha256-"))

    def test_parses_edo_margin_and_capitalization(self):
        html = """
        <p>Seria: EDO0936</p>
        <p>Oprocentowanie: 5,35% w pierwszym rocznym okresie odsetkowym,
        w kolejnych rocznych okresach odsetkowych: marża 2,00% + inflacja</p>
        <p>Sprzedaż: 01.09.2026 - 30.09.2026</p>
        <p>Cena sprzedaży jednej obligacji: 100,00 zł</p>
        """
        item = parse_series_detail(
            FAMILIES["EDO"], "EDO0936", html,
            "https://www.obligacjeskarbowe.pl/oferta-obligacji/obligacje-10-letnie-edo/edo0936/",
            "2026-09-03",
        )
        self.assertEqual(0.0535, item["firstPeriodAnnualRate"])
        self.assertEqual(0.02, item["rateRule"]["margin"])
        self.assertEqual("EndOfPeriod", item["capitalizationRule"])

    def test_ots_requires_and_parses_fixed_maturity_interest(self):
        html = """
        <p>Seria: OTS1226</p><p>Oprocentowanie: 2,00% w skali roku</p>
        <p>Sprzedaż: 01.09.2026 - 30.09.2026</p>
        <p>Cena sprzedaży jednej obligacji: 100,00 zł</p>
        <p>Odsetki: 0,50 zł</p>
        """
        item = parse_series_detail(
            FAMILIES["OTS"], "OTS1226", html,
            "https://www.obligacjeskarbowe.pl/oferta-obligacji/obligacje-3-miesieczne-ots/ots1226/",
            "2026-09-03",
        )
        self.assertEqual(0.5, item["fixedMaturityInterestPerBond"])
        self.assertEqual("FixedMaturityOnly", item["accrualRule"])

    def test_semantic_comparison_ignores_verification_metadata(self):
        left = {"seriesCode": "ROR0927", "termsVersion": "one", "source": "a", "verifiedAt": "2026-09-01", "rateRule": {"kind": "Fixed", "margin": 0}}
        right = {"seriesCode": "ROR0927", "termsVersion": "two", "source": "b", "verifiedAt": "2026-09-03", "rateRule": {"kind": "Fixed", "margin": 0}}
        self.assertTrue(semantically_equal(left, right))


if __name__ == "__main__":
    unittest.main()
