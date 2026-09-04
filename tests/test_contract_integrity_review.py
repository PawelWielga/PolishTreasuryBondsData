import copy
import unittest

import scripts.pipeline as pipeline


class CatalogRevisionIntegrityTests(unittest.TestCase):
    def test_series_terms_revision_gap_is_rejected(self):
        original = copy.deepcopy(pipeline.load_series()[0])
        skipped = copy.deepcopy(original)
        skipped["termsRevision"] = 3
        skipped["contentHash"] = pipeline.terms_content_hash(skipped)

        with self.assertRaisesRegex(ValueError, r"terms revisions must be contiguous from 1"):
            pipeline._validate_series(
                [original, skipped],
                pipeline.load_product_definitions(),
            )


if __name__ == "__main__":
    unittest.main()
