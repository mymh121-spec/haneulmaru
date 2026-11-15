import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
import yaml

from .datasets import Sample, read_coco, read_yolo, write_dataset


class DatasetValidationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=".")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.output = self.root / "export"
        self.box = (0, .5, .5, .2, .2)
        for name, color in (("train", "red"), ("val", "blue")):
            Image.new("RGB", (20, 10), color).save(self.root / (name + ".png"))

    def splits(self):
        return {s: [Sample(self.root / (s + ".png"), [self.box])] for s in ("train", "val")}

    def assert_unpublished(self):
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".dataset-*")), [])

    def test_renamed_identical_images_are_rejected_across_splits(self):
        (self.root / "val.png").write_bytes((self.root / "train.png").read_bytes())
        with self.assertRaisesRegex(ValueError, "Duplicate image content: val:.*val.png matches train:.*train.png"):
            write_dataset(self.output, ["drone"], self.splits())
        self.assert_unpublished()

    def test_yolo_outside_edges_are_rejected_with_label_line(self):
        data = self.root / "data.yaml"
        data.write_text(yaml.safe_dump(dict(names=["drone"], train="train.png", val="val.png")))
        (self.root / "val.txt").write_text("0 .5 .5 .2 .2\n")
        for row in ("0 .95 .5 .4 .2", "0 .05 .5 .4 .2", "0 .5 .95 .2 .4", "0 .5 .05 .2 .4"):
            with self.subTest(row=row):
                (self.root / "train.txt").write_text(row)
                with self.assertRaisesRegex(ValueError, r"outside image.*train.txt:1"):
                    write_dataset(self.output, *read_yolo(data))
                self.assert_unpublished()

    def test_writer_also_rejects_outside_boxes(self):
        splits = self.splits()
        splits["val"][0].boxes = [(0, .95, .5, .4, .2)]
        with self.assertRaisesRegex(ValueError, "outside image.*val.png"):
            write_dataset(self.output, ["drone"], splits)
        self.assert_unpublished()

    def coco(self, width=20):
        inputs = {}
        for split in ("train", "val"):
            doc = dict(images=[dict(id=1, file_name=split+".png", width=width, height=10)],
                       categories=[dict(id=7, name="drone")],
                       annotations=[dict(id=3, image_id=1, category_id=7, bbox=[-1, 2, 9, 4])])
            path = self.root / (split + ".json")
            path.write_text(json.dumps(doc))
            inputs[split] = (path, self.root)
        return inputs

    def test_coco_dimensions_are_checked_against_image(self):
        with self.assertRaisesRegex(ValueError, "dimensions mismatch.*train.png"):
            write_dataset(self.output, *read_coco(self.coco(width=21)))
        self.assert_unpublished()

    def test_coco_clipping_is_reported_and_class_mapping_preserved(self):
        counts = write_dataset(self.output, *read_coco(self.coco()))
        report = json.loads((self.output / "validation_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["counts"], counts)
        self.assertEqual(report["clipped_boxes"], 2)
        self.assertEqual(report["samples"][0]["source"], str(self.root / "train.png"))
        names, splits = read_yolo(self.output / "data.yaml")
        self.assertEqual(names, ["drone"])
        self.assertEqual(next(splits["val"]).boxes, [(0, .2, .4, .4, .4)])

    def test_corrupt_image_is_rejected_atomically(self):
        (self.root / "val.png").write_bytes(b"not an image")
        with self.assertRaisesRegex(ValueError, "Unreadable dataset image"):
            write_dataset(self.output, ["drone"], self.splits())
        self.assert_unpublished()

    def test_existing_output_is_preserved(self):
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("existing data")
        with self.assertRaisesRegex(ValueError, "new output directory"):
            write_dataset(self.output, ["drone"], self.splits())
        self.assertEqual(sentinel.read_text(), "existing data")


if __name__ == "__main__":
    unittest.main()
