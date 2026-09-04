import unittest

from scripts import pipeline


class GusTemporalIntegrityTests(unittest.TestCase):
    @staticmethod
    def observation(period: str) -> dict:
        year, month = (int(part) for part in period.split("-"))
        return {
            "period": period,
            "indexPreviousYear100": "103.00",
            "yearOverYearPercent": "3.00",
            "revision": 1,
            "source": {
                "publisher": "GUS",
                "api": "SDP",
                "year": year,
                "periodId": 246 + month,
            },
        }

    def test_offline_validation_rejects_future_month_within_verification_year(self):
        source = {
            "verifiedAt": "2014-01-15T00:00:00Z",
            "observations": [self.observation("2014-01"), self.observation("2014-02")],
        }

        with self.assertRaisesRegex(ValueError, "future period 2014-02"):
            pipeline._validate_gus(source)


if __name__ == "__main__":
    unittest.main()
