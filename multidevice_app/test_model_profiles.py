import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image
import yaml

from .contracts import Frame, SetupError
from .datasets import read_coco, read_yolo, write_dataset
from .model_profile import load_profile
from .rust_host import RustEngine
from .test_rust_host import field, io, node
from .tracking import create_tracker


LABELS = ["crate", "cone", "cart", "표지판"]


def graph(values):
    data = np.ascontiguousarray(values, dtype="<f4")
    tensor = (b"".join(field(1,d) for d in data.shape) + field(2,1) + field(8,"boxes")
              + field(9,data.tobytes()))
    g = (field(1,node("Identity",["boxes"],"predictions",[])) + field(2,"profile-test")
         + field(5,tensor) + field(11,io("frame",[1,3,32,32])) + field(12,io("predictions",data.shape)))
    return field(1,7) + field(7,g) + field(8,field(2,11))


class ProfileTests(unittest.TestCase):
    def test_four_classes_all_layouts_and_tracking(self):
        # Coincident objects of different classes must survive class-aware NMS.
        raw = [[16,16,8,8,.9,0,0,0],[16,16,8,8,0,0,0,.8]]
        obj = [r[:4]+[.8]+r[4:] for r in raw]
        post = [[12,12,20,20,.9,0],[12,12,20,20,.8,3],[0,0,0,0,0,-1]]
        cases = [("yolo_raw",np.asarray(raw).T),("yolo_raw_rows",raw),
                 ("yolo_objectness_rows",obj),("xyxy_score_class",post)]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for layout, rows in cases:
                with self.subTest(layout=layout):
                    (root/"weights.onnx").write_bytes(graph(np.asarray(rows)[None]))
                    profile=dict(schema_version=1, model_path="weights.onnx", classes=LABELS, input_size=32,
                                 input_name="frame",output_name="predictions",output_layout=layout)
                    (root/"model.json").write_text(json.dumps(profile),encoding="utf-8")
                    cfg=dict(confidence=.1,nms_iou=.7,**load_profile(root/"model.json"))
                    engine,tracker=RustEngine(cfg,root),create_tracker("rust",LABELS)
                    try:
                        for i in range(2):
                            frame=Frame(i,50*i,np.zeros((32,32,3),np.uint8))
                            tracks=tracker.update(frame,engine.detect(frame))
                            for d in tracks: d.validate(32,32)
                            self.assertEqual([(d.code,d.id,d.xyxy) for d in tracks],
                                    [("crate",1,(12,12,20,20)),("표지판",2,(12,12,20,20))])
                            self.assertAlmostEqual(tracks[0].confidence,.72 if layout=="yolo_objectness_rows" else .9,places=5)
                    finally:
                        engine.close()
                        tracker.close()

    def test_sparse_coco_ids_and_yolo_round_trip(self):
        with tempfile.TemporaryDirectory(dir=".") as tmp:
            root=Path(tmp)
            paths={}
            for split in ["train","val"]:
                fname=split+".png"
                Image.new("RGB",(20,10)).save(root/fname)
                categories=[{"id":70,"name":"표지판"},{"id":5,"name":"crate"}]
                if split=="val": categories.reverse()
                doc=dict(images=[dict(id=3,file_name=fname,width=20,height=10)],categories=categories,
                         annotations=[dict(id=1,image_id=3,category_id=70,bbox=[-1,2,9,4])])
                path=root/(split+".json")
                path.write_text(json.dumps(doc),encoding="utf-8")
                paths[split]=(path,root)
            names,splits=read_coco(paths); out=root/"export"
            counts=write_dataset(out,names,splits)
            self.assertEqual(names,["crate","표지판"])
            self.assertEqual(counts["train"],{"images":1,"boxes":1})
            self.assertEqual(load_profile(out/"profile.json")["classes"],names)
            names2,splits2=read_yolo(out/"data.yaml")
            self.assertEqual(names,names2)
            sample=next(splits2["val"])
            self.assertEqual(sample.boxes,[(1,.2,.4,.4,.4)])
            with self.assertRaises(ValueError):write_dataset(out,names,splits)

    def test_invalid_label_does_not_publish_partial_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for s in ("train","val"):
                (root/"images"/s).mkdir(parents=True)
                (root/"labels"/s).mkdir(parents=True)
                Image.new("RGB",(20,10)).save(root/"images"/s/"example.png")
                (root/"labels"/s/"example.txt").write_text(("0" if s=="train" else "9")+" .5 .5 .1 .1")
            data=root/"data.yaml"; data.write_text(yaml.safe_dump(dict(names={0:"crate"},train="images/train",val="images/val")))
            out=root/"imported"
            with self.assertRaisesRegex(ValueError,"expected class_id"):
                write_dataset(out,*read_yolo(data))
            self.assertFalse(out.exists())

    def test_invalid_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"model.json"
            for overrides in ({"classes":["crate","crate"]},{"classes":["UNKNOWN"]},{"output_layout":"segment"}):
                profile=dict(schema_version=1,model_path="best.onnx",input_size=32,classes=LABELS,output_layout="yolo_raw")
                profile.update(overrides)
                p.write_text(json.dumps(profile))
                with self.assertRaises(SetupError): load_profile(p)
