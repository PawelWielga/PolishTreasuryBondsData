from __future__ import annotations

import unittest

import requests


class GusLiveProbe(unittest.TestCase):
    def test_probe_variable_year_without_period(self) -> None:
        base = "https://api-sdp.stat.gov.pl/api/1.1.0"
        session = requests.Session()
        session.headers.update({"User-Agent": "PolishTreasuryBondsData-live-probe"})

        response = session.get(
            f"{base}/variable/variable-data-section",
            params={
                "id-zmienna": 305,
                "id-przekroj": 1698,
                "id-rok": 2026,
                "page-size": 1,
                "page": 1,
                "lang": "pl",
            },
            timeout=20,
        )
        print(
            f"GUS_PROBE_NO_PERIOD status={response.status_code} bytes={len(response.content)}"
        )
        print("GUS_PROBE_NO_PERIOD_BODY=" + response.text[:20000])
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
