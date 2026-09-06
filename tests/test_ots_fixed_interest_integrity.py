import unittest

from scripts.sources import SourceError, cross_check_series, parse_series_html


class OtsFixedMaturityInterestCrossCheckTests(unittest.TestCase):
    def test_ots_fixed_maturity_interest_is_parsed_from_official_summary(self):
        html = """
        <main>
          <h1>3-miesięczne obligacje OTS</h1>
          <p>Seria: OTS1226</p>
          <p>Oprocentowanie: 2,00%</p>
          <p>Sprzedaż: 01.09.2026 - 30.09.2026</p>
          <p>Cena sprzedaży jednej obligacji: 100,00 zł</p>
          <p>Cena zamiany jednej obligacji: 100,00 zł</p>
          <p>Odsetki: 0,50 zł</p>
        </main>
        """

        facts = parse_series_html(html)

        self.assertEqual(50, facts["fixedMaturityInterestMinorUnits"])

    def test_ots_missing_fixed_maturity_interest_fails_closed(self):
        html = """
        <main>
          <h1>3-miesięczne obligacje OTS</h1>
          <p>Seria: OTS1226</p>
          <p>Oprocentowanie: 2,00%</p>
          <p>Sprzedaż: 01.09.2026 - 30.09.2026</p>
          <p>Cena sprzedaży jednej obligacji: 100,00 zł</p>
          <p>Cena zamiany jednej obligacji: 100,00 zł</p>
        </main>
        """

        with self.assertRaisesRegex(SourceError, "fixed maturity interest"):
            parse_series_html(html)

    def test_ots_maturity_interest_disagreement_between_official_sources_is_rejected(self):
        workbook = {
            "seriesCode": "OTS1226",
            "productType": "OTS",
            "exchangePriceMinorUnits": 10000,
            "fixedMaturityInterestMinorUnits": 50,
        }
        html_facts = {
            "exchangePriceMinorUnits": 10000,
            "fixedMaturityInterestMinorUnits": 49,
        }

        with self.assertRaisesRegex(SourceError, "official sources disagree on fixedMaturityInterestMinorUnits"):
            cross_check_series(workbook, html_facts)


if __name__ == "__main__":
    unittest.main()
