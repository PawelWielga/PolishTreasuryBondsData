from __future__ import annotations

import json
import unittest

import requests


class GusLiveProbe(unittest.TestCase):
    def test_probe_indicator_1832_for_2025_and_2026(self) -> None:
        base = "https://api-sdp.stat.gov.pl/api/1.1.0"
        session = requests.Session()
        session.headers.update({"User-Agent": "PolishTreasuryBondsData-live-probe"})

        for year in (2025, 2026):
            response = session.get(
                f"{base}/indicators/indicator-data-indicator",
                params={"id-wskaznik": 1832, "id-rok": year, "lang": "pl"},
                timeout=20,
            )
            print(
                f"GUS_PROBE_1832 year={year} status={response.status_code} bytes={len(response.content)}"
            )
            body = response.text
            print(f"GUS_PROBE_1832_BODY year={year}=" + body[:20000])

        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
