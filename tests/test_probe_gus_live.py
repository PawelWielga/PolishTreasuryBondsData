from __future__ import annotations

import json
import unittest

import requests


class GusLiveProbe(unittest.TestCase):
    def test_probe_current_indicator_options(self) -> None:
        base = "https://api-sdp.stat.gov.pl/api/1.1.0"
        session = requests.Session()
        session.headers.update({"User-Agent": "PolishTreasuryBondsData-live-probe"})

        response = session.get(
            f"{base}/indicators/indicator-indicator-variable",
            params={"id-zmienna": 305, "lang": "pl"},
            timeout=30,
        )
        response.raise_for_status()
        indicators = response.json()
        print("GUS_PROBE_INDICATORS=" + json.dumps(indicators, ensure_ascii=False))

        ids: list[int] = []
        if isinstance(indicators, list):
            for item in indicators:
                if not isinstance(item, dict):
                    continue
                for key in ("id-wskaznik", "id", "idWskaznik"):
                    value = item.get(key)
                    if isinstance(value, int):
                        ids.append(value)
                        break

        self.assertTrue(ids, "No indicator IDs returned for variable 305")
        for indicator_id in sorted(set(ids)):
            data_response = session.get(
                f"{base}/indicators/indicator-data-indicator",
                params={"id-wskaznik": indicator_id, "id-rok": 2026, "lang": "pl"},
                timeout=30,
            )
            print(
                f"GUS_PROBE_DATA_STATUS id={indicator_id} status={data_response.status_code} bytes={len(data_response.content)}"
            )
            if data_response.status_code == 200:
                payload = data_response.json()
                print(
                    f"GUS_PROBE_DATA id={indicator_id}="
                    + json.dumps(payload, ensure_ascii=False)[:20000]
                )


if __name__ == "__main__":
    unittest.main()
