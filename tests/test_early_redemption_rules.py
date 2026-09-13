from __future__ import annotations

import copy
import json
import unittest
from datetime import date
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from scripts import pipeline


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = json.loads((ROOT / "data" / "early-redemption-rules.json").read_text(encoding="utf-8"))
SCHEMA = json.loads(
    (ROOT / "schemas" / "early-redemption-rules-v2.schema.json").read_text(encoding="utf-8")
)


def resolve(product_type: str, purchase_date: str) -> dict:
    value = date.fromisoformat(purchase_date)
    matches = []
    for rule in CANONICAL["rules"]:
        if rule["productType"] != product_type:
            continue
        start = date.fromisoformat(rule["purchaseFrom"])
        end = date.max if rule["purchaseThrough"] is None else date.fromisoformat(rule["purchaseThrough"])
        if start <= value <= end:
            matches.append(rule)
    if len(matches) != 1:
        raise AssertionError(
            f"Expected exactly one {product_type} early-redemption rule for {purchase_date}, got {len(matches)}"
        )
    return matches[0]


class EarlyRedemptionRuleTests(unittest.TestCase):
    def test_public_projection_satisfies_schema(self) -> None:
        document = {
            "schemaVersion": "2.0",
            "generatedAt": CANONICAL["verifiedAt"] + "T00:00:00Z",
            "rules": CANONICAL["rules"],
        }
        validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
        self.assertEqual([], list(validator.iter_errors(document)))

    def test_pipeline_semantic_validation_accepts_canonical_rules(self) -> None:
        pipeline._validate_early_redemption_rules(CANONICAL, pipeline.load_series())

    def test_every_catalog_series_is_covered_by_exactly_one_rule(self) -> None:
        for series in pipeline.load_series():
            for boundary in (series["saleFrom"], series["saleTo"]):
                rule = resolve(series["productType"], boundary)
                self.assertLessEqual(rule["purchaseFrom"], series["saleFrom"])
                if rule["purchaseThrough"] is not None:
                    self.assertGreaterEqual(rule["purchaseThrough"], series["saleTo"])

    def test_provenance_series_exist_and_match_family(self) -> None:
        known = {series["seriesCode"] for series in pipeline.load_series()}
        for rule in CANONICAL["rules"]:
            code = rule["provenance"]["seriesCode"]
            self.assertIn(code, known)
            self.assertTrue(code.startswith(rule["productType"]))

    def test_edo_cutoff_and_fee_boundaries(self) -> None:
        june = resolve("EDO", "2022-06-30")
        july = resolve("EDO", "2022-07-01")
        august_2024 = resolve("EDO", "2024-08-31")
        september_2024 = resolve("EDO", "2024-09-01")

        self.assertEqual("CalendarMonthsBeforeMaturity", june["latestInstruction"]["unit"])
        self.assertEqual(1, june["latestInstruction"]["value"])
        self.assertEqual("CalendarDaysBeforeMaturity", july["latestInstruction"]["unit"])
        self.assertEqual(20, july["latestInstruction"]["value"])
        self.assertEqual(200, august_2024["chargeMinorUnits"])
        self.assertEqual(300, september_2024["chargeMinorUnits"])

    def test_family_bonds_share_2022_cutoff_and_2024_fee_boundaries(self) -> None:
        expectations = {
            "ROS": (70, 200),
            "ROD": (200, 300),
        }
        for family, (old_fee, new_fee) in expectations.items():
            with self.subTest(family=family, boundary="2022-07"):
                before = resolve(family, "2022-06-30")
                after = resolve(family, "2022-07-01")
                self.assertEqual("CalendarMonthsBeforeMaturity", before["latestInstruction"]["unit"])
                self.assertEqual("CalendarDaysBeforeMaturity", after["latestInstruction"]["unit"])
                self.assertEqual(20, after["latestInstruction"]["value"])
            with self.subTest(family=family, boundary="2024-09"):
                self.assertEqual(old_fee, resolve(family, "2024-08-31")["chargeMinorUnits"])
                self.assertEqual(new_fee, resolve(family, "2024-09-01")["chargeMinorUnits"])

    def test_tos_and_coi_fee_boundaries(self) -> None:
        self.assertEqual(70, resolve("TOS", "2024-08-31")["chargeMinorUnits"])
        self.assertEqual(100, resolve("TOS", "2024-09-01")["chargeMinorUnits"])
        self.assertEqual(70, resolve("COI", "2024-08-31")["chargeMinorUnits"])
        self.assertEqual(200, resolve("COI", "2024-09-01")["chargeMinorUnits"])

    def test_coupon_families_only_cap_charge_in_first_period(self) -> None:
        for family, fee in (("ROR", 50), ("DOR", 70), ("COI", 200)):
            rule = resolve(family, "2026-09-01")
            self.assertEqual(fee, rule["chargeMinorUnits"])
            self.assertEqual(
                "FirstPeriodDeductUpToAccruedInterestThenFullCharge",
                rule["chargeApplication"],
            )
            self.assertEqual(["InterestEntitlementDate"], rule["excludedInstructionDates"])

    def test_capital_protected_families_cap_charge_by_accrued_interest(self) -> None:
        for family in ("TOS", "EDO", "ROS", "ROD"):
            rule = resolve(family, "2026-09-01")
            self.assertEqual("DeductUpToAccruedInterest", rule["chargeApplication"])

    def test_ots_returns_principal_without_early_redemption_interest_or_charge(self) -> None:
        rule = resolve("OTS", "2026-09-01")
        self.assertEqual(0, rule["chargeMinorUnits"])
        self.assertEqual("NoCharge", rule["chargeApplication"])
        self.assertEqual({"kind": "None"}, rule["interestAccrualTiming"])

    def test_semantic_validation_rejects_a_gap(self) -> None:
        broken = copy.deepcopy(CANONICAL)
        edo_second = next(
            rule
            for rule in broken["rules"]
            if rule["productType"] == "EDO" and rule["rulesRevision"] == 2
        )
        edo_second["purchaseFrom"] = "2022-07-02"
        with self.assertRaisesRegex(ValueError, "contiguous"):
            pipeline._validate_early_redemption_rules(broken, pipeline.load_series())

    def test_semantic_validation_rejects_overlapping_rules(self) -> None:
        broken = copy.deepcopy(CANONICAL)
        edo_second = next(
            rule
            for rule in broken["rules"]
            if rule["productType"] == "EDO" and rule["rulesRevision"] == 2
        )
        edo_second["purchaseFrom"] = "2022-06-30"
        with self.assertRaisesRegex(ValueError, "contiguous"):
            pipeline._validate_early_redemption_rules(broken, pipeline.load_series())

    def test_semantic_validation_rejects_uncovered_catalog_series(self) -> None:
        broken = copy.deepcopy(CANONICAL)
        broken["rules"] = [rule for rule in broken["rules"] if rule["productType"] != "OTS"]
        with self.assertRaisesRegex(ValueError, "supported families"):
            pipeline._validate_early_redemption_rules(broken, pipeline.load_series())


if __name__ == "__main__":
    unittest.main()
