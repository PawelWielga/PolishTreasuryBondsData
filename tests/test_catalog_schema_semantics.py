import copy
import unittest

from scripts import pipeline


class CatalogSchemaSemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = pipeline.load_json(pipeline.SCHEMAS / "catalog-v2.schema.json")
        cls.series = pipeline.load_series()
        cls.products = pipeline.load_product_definitions()

    def validate_item(self, item: dict) -> None:
        document = {
            "schemaVersion": "2.0",
            "generatedAt": "2026-09-05T00:00:00Z",
            "series": [item],
        }
        pipeline._validate_schema(document, self.schema, "catalog.json")

    def test_catalog_cannot_be_empty(self) -> None:
        document = {
            "schemaVersion": "2.0",
            "generatedAt": "2026-09-05T00:00:00Z",
            "series": [],
        }
        with self.assertRaises(ValueError):
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

    def test_family_bonds_reject_exchange_price(self) -> None:
        item = copy.deepcopy(next(row for row in self.series if row["productType"] == "ROS"))
        item["exchangePriceMinorUnits"] = 9990
        with self.assertRaises(ValueError):
            self.validate_item(item)

    def test_non_family_bonds_require_exchange_price(self) -> None:
        item = copy.deepcopy(next(row for row in self.series if row["productType"] == "ROR"))
        item["exchangePriceMinorUnits"] = None
        with self.assertRaises(ValueError):
            self.validate_item(item)

    def test_fixed_rate_products_reject_nonzero_margin(self) -> None:
        item = copy.deepcopy(next(row for row in self.series if row["productType"] == "TOS"))
        item["marginPercent"] = "0.01"
        with self.assertRaises(ValueError):
            self.validate_item(item)

    def test_schema_rejects_series_prefix_mismatch(self) -> None:
        item = copy.deepcopy(next(row for row in self.series if row["productType"] == "ROR"))
        item["seriesCode"] = "DOR" + item["seriesCode"][3:]
        with self.assertRaises(ValueError):
            self.validate_item(item)

    def test_offline_semantics_reject_impossible_maturity_suffix(self) -> None:
        item = copy.deepcopy(next(row for row in self.series if row["productType"] == "ROR"))
        month = int(item["seriesCode"][3:5])
        wrong_month = month % 12 + 1
        item["seriesCode"] = f"ROR{wrong_month:02d}{item['seriesCode'][5:]}"
        with self.assertRaisesRegex(ValueError, "maturity suffix disagrees"):
            pipeline._validate_series([item], self.products)


if __name__ == "__main__":
    unittest.main()
