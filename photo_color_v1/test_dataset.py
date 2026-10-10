import tempfile
import unittest
from pathlib import Path
from PIL import Image
from build_dataset import checked_box, normalized_box, select_diverse, add_marker


class DatasetRules(unittest.TestCase):
    def test_clips_partial_box(self):
        self.assertEqual(checked_box([-4, 10, 24, 40], 100, 100), [0, 10, 20, 40])

    def test_rejects_invalid_box(self):
        for box in ([0, 0, -3, 4], [0, 0, float('nan'), 4], [120, 0, 3, 4]):
            with self.subTest(box=box), self.assertRaises(ValueError):
                checked_box(box, 100, 100)

    def test_yolo_conversion(self):
        self.assertEqual(normalized_box([10, 20, 40, 20], 100, 100), [.3, .3, .4, .2])

    def test_similar_candidates_excluded_before_split(self):
        items = [{'source_name': str(i), 'pixel_sha256': str(i), 'dhash64': h} for i, h in enumerate((0, 1, (1 << 64)-1))]
        chosen, excluded = select_diverse(items, 2, 7)
        self.assertEqual(len(chosen), 2)
        self.assertGreater((chosen[0]['dhash64'] ^ chosen[1]['dhash64']).bit_count(), 4)

    def test_marker_changes_only_its_box(self):
        original = Image.new('RGB', (100, 100), (120, 120, 120))
        marked = add_marker(original, [30, 40, 20, 20], 'red', 1)
        self.assertEqual(marked.getpixel((5, 5)), original.getpixel((5, 5)))
        self.assertEqual(original.getpixel((40, 50)), (120, 120, 120))
        r, g, b = marked.getpixel((40, 50))
        self.assertGreater(r, g * 3)
        self.assertGreater(r, b * 3)


if __name__ == '__main__':
    unittest.main()
