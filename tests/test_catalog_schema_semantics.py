import copy
import unittest

from scripts import pipeline


class CatalogSchemaSemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = pipeline.load_json(pipeline.SCHEMAS / "catalog-v2.schema.json")
        cls.series = pipeline.load_series()

    def validate_item(self, item: dict) -> None:
        document = {
            "schemaVersion": "2.0",
            "generatedAt": "2026-09-05T00:00:00Z",
            "series": [item],
        }
        pipeline._validate_schema(document, self.schema, "catalog.json")

    def test_face_value_is_fixed_at_100_pln(self) -> None:
        item = copy.deepcopy(self.series[0])
        item["faceValueMinorUnits"] = 9999
        with self.assertRaisesRegex(ValueError, "10000 was expected"):
            self.validate_item(item)

    def test_ots_requires_fixed_maturity_interest(self) -> None:
        item = copy.deepcopy(next(row for row in self.series if row["productType"] == "OTS"))
        item["fixedMaturityInterestMinorUnits"] = None
        with self.assertRaises(ValueError):
            self.validate_item(item)

    def test_non_ots_rejects_fixed_maturity_interest(self) -> None:
        item = copy.deepcopy(next(row for row in self.series if row["productType"] != "OTS"))
        item["fixedMaturityInterestMinorUnits"] = 1
        with self.assertRaisesRegex(ValueError, "None was expected"):
            self.validate_item(item)


if __name__ == "__main__":
    unittest.main()
