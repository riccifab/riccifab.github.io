import unittest

from lab_names import canonical_lab_name, clean_lab_name, normalize_lab_key


class LabNamesTest(unittest.TestCase):
    def test_known_lab_variants_share_one_key(self) -> None:
        variants = ["Iurilli", "iurilli", "IURILLI", " Iurilli Lab ", "Iurilli-lab"]
        self.assertEqual({normalize_lab_key(value) for value in variants}, {"iurilli"})
        self.assertEqual({canonical_lab_name(value) for value in variants}, {"Iurilli"})

    def test_custom_lab_keeps_a_clean_display_name(self) -> None:
        self.assertEqual(clean_lab_name("  Advanced   Imaging  "), "Advanced Imaging")
        self.assertEqual(canonical_lab_name("  Advanced   Imaging  "), "Advanced Imaging")
        self.assertEqual(normalize_lab_key("Advanced Imaging Lab"), "advancedimaging")

    def test_diacritics_are_stable(self) -> None:
        self.assertEqual(normalize_lab_key("Müller Lab"), "muller")


if __name__ == "__main__":
    unittest.main()
