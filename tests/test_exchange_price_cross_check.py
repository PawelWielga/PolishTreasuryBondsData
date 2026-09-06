import unittest

from scripts.sources import SourceError, cross_check_series, parse_series_html


ROR_HTML = """
<main>
  <h1>Obligacje roczne ROR</h1>
  <p>Seria: ROR0927</p>
  <p>Oprocentowanie: 4,00%</p>
  <p>W kolejnych miesięcznych okresach odsetkowych: stopa referencyjna NBP+0,00%.</p>
  <p>Sprzedaż: 01.09.2026 - 30.09.2026</p>
  <p>Cena sprzedaży jednej obligacji: 100,00 zł</p>
  <p>Cena zamiany jednej obligacji: 99,90 zł</p>
</main>
"""


def workbook_ror(exchange_price: int = 9990) -> dict:
    return {
        "seriesCode": "ROR0927",
        "productType": "ROR",
        "saleFrom": "2026-09-01",
        "saleTo": "2026-09-30",
        "issuePriceMinorUnits": 10000,
        "exchangePriceMinorUnits": exchange_price,
        "firstPeriodAnnualRatePercent": "4.00",
        "marginPercent": "0.00",
        "fixedMaturityInterestMinorUnits": None,
    }


class ExchangePriceCrossCheckTests(unittest.TestCase):
    def test_standard_offer_parser_extracts_exchange_price(self) -> None:
        facts = parse_series_html(ROR_HTML)
        self.assertEqual(9990, facts["exchangePriceMinorUnits"])

    def test_standard_offer_missing_exchange_price_fails_closed(self) -> None:
        html = ROR_HTML.replace(
            '<p>Cena zamiany jednej obligacji: 99,90 zł</p>',
            '',
        )
        facts = parse_series_html(html)
        self.assertIsNone(facts["exchangePriceMinorUnits"])
        with self.assertRaisesRegex(SourceError, "exchangePriceMinorUnits"):
            cross_check_series(workbook_ror(), facts)

    def test_exchange_price_disagreement_fails_cross_check(self) -> None:
        facts = parse_series_html(ROR_HTML)
        with self.assertRaisesRegex(SourceError, "exchangePriceMinorUnits"):
            cross_check_series(workbook_ror(9980), facts)


if __name__ == "__main__":
    unittest.main()
