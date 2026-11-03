import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from PIL import Image
from requests import Response

from .model_profile import load_profile
from .remote_datasets import import_remote


def png(color):
    output = io.BytesIO()
    Image.new("RGB", (20, 10), color).save(output, "PNG")
    return output.getvalue()


def response(body, status=200, revision=None, **headers):
    r = Response()
    r.status_code = status
    r.headers.update(headers)
    if revision is not None:
        r.headers["X-Revision"] = revision
    if not isinstance(body, bytes):
        body = json.dumps(body).encode()
    r.raw = io.BytesIO(body)
    return r


def generic():
    return dict(schema_version=1, provider="http", base_url="https://api.example/v1/",
                splits={"train":"train", "val":"val"}, classes=["상자","표지판"], category_ids=[5,70],
                fields=dict(image="url", boxes="annotations.box", labels="annotations.category"),
                bbox_format="xyxy_pixels", asset_origins=["https://cdn.example"], token_env="HM_TEST_TOKEN")


def item(key, label=70):
    return dict(id=key, url="/assets/"+key, annotations=[{"box":[2,1,10,5],"category":label}])


class RemoteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=".")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.out = self.root / "published"

    def ingest(self, spec, handler, limit=None):
        cfg = self.root/"source.json"
        cfg.write_text(json.dumps(spec), encoding="utf-8")
        with patch.dict(os.environ, HM_TEST_TOKEN="test-secret"), patch("requests.Session.get", side_effect=handler), \
                patch("multidevice_app.remote_datasets.time.sleep"):
            return import_remote(cfg,self.out,limit)

    def test_cursor_url_and_token_scope_and_provenance(self):
        calls=[]
        spec=generic()
        def get(url, *, headers, **_):
            calls.append(url)
            p=urlsplit(url)
            if p.hostname=="cdn.example":
                self.assertNotIn("Authorization",headers)
                return response(png("green"))
            self.assertEqual(headers.get("Authorization"),"Bearer test-secret")
            if p.path.startswith("/assets/"):
                if p.path.endswith("/a"):
                    return response({},302,Location="https://cdn.example/asset?sig=temporary")
                return response(png("red" if p.path.endswith("/b") else "blue"))
            if p.path.endswith("/val"):
                return response({"items":[item("c",5)],"next":None},revision="v1")
            if not p.query:
                return response({"items":[item("a")],"next":"?after=b"},revision="v1")
            return response({"items":[dict(item("b"),annotations=[])],"next":None},revision="v1")
        counts=self.ingest(spec,get)
        self.assertEqual(counts,{"train":{"images":2,"boxes":1},"val":{"images":1,"boxes":1}})
        self.assertIn("https://api.example/v1/train?after=b",calls)
        self.assertEqual((self.out/"labels/train/0000001.txt").read_text().split(),["1","0.3","0.3","0.4","0.4"])
        self.assertEqual(load_profile(self.out/"profile.json")["classes"],["상자","표지판"])
        raw=(self.out/"source.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(raw)["revision"],"v1")
        self.assertNotIn("test-secret",raw)
        self.assertNotIn("sig=",raw)

    def test_custom_envelope_and_opaque_cursor(self):
        spec=generic()
        spec["pagination"]=dict(items="data.samples",next="links.cursor",cursor_param="after")
        visits=[]
        def get(url,**_):
            p=urlsplit(url)
            if p.path.startswith("/assets/"):
                return response(png({"a":"red","b":"green","v":"blue"}[p.path.split("/")[-1]]))
            query=parse_qs(p.query);visits.append((p.path,query))
            val=p.path.endswith("val")
            key="v" if val else "b" if query else "a"
            cursor=None if key!="a" else "a+b/c="
            return response({"data":{"samples":[item(key)]},"links":{"cursor":cursor}})
        self.ingest(spec,get)
        self.assertIn(("/v1/train",{"after":["a+b/c="]}),visits)

    def test_failures_do_not_publish_or_leave_temporary_images(self):
        failures = {"http": "HTTP 503", "image": "identify image", "unknown-class": "Unknown category",
                    "duplicate-id": "row ID", "duplicate-image": "Image repeated", "revision": "revision",
                    "repeat-page": "Repeated API page", "cross-origin": "allow this host", "redirect": "allow this host"}
        for failure, message in failures.items():
            with self.subTest(failure=failure):
                def get(url,**_):
                    p=urlsplit(url)
                    if p.hostname!="api.example":
                        self.fail("request followed an unapproved host")
                    if p.path.startswith("/assets/"):
                        if failure=="image" and p.path.endswith("b"): return response(b"invalid")
                        return response(png("red" if p.path.endswith("a") or failure=="duplicate-image" else "blue"))
                    if not p.query:
                        return response({"items":[item("a")],"next":"?p=2"},revision="v1")
                    if failure=="http":return response({},503)
                    if failure=="cross-origin":return response({"items":[item("b")],"next":"https://other.example/page"},revision="v1")
                    if failure=="redirect":return response({},302,Location="https://other.example/")
                    if failure=="repeat-page":return response({"items":[item("b")],"next":"?p=2"},revision="v1")
                    return response({"items":[item("a" if failure=="duplicate-id" else "b",
                                                   66 if failure=="unknown-class" else 70)],"next":None},
                                    revision="v2" if failure=="revision" else "v1")
                with self.assertRaisesRegex(ValueError, message):
                    self.ingest(generic(),get)
                self.assertEqual({p.name for p in self.root.iterdir()},{"source.json"})

    def test_normalized_boxes_and_string_labels(self):
        cfg = generic()
        cfg["bbox_format"] = "cxcywh_normalized"
        def get(url, **_):
            split = urlsplit(url).path.split("/")[-1]
            if "/assets/" in url:
                return response(png("red" if split == "train" else "blue"))
            row = item(split)
            row["annotations"] = [{"box": [.5, .5, .2, .4], "category": "표지판"}]
            return response({"items": [row], "next": None})
        self.ingest(cfg, get)
        self.assertEqual((self.out/"labels/train/0000001.txt").read_text().split(), ["1","0.5","0.5","0.2","0.4"])

    def test_hf_pagination_retries_revision_and_classlabel(self):
        spec=json.loads((Path(__file__).parent/"examples/hf-sheep.json").read_text(encoding="utf-8"))
        spec["page_size"]=1
        del spec["fields"]["id"]  # Use the Viewer row index on datasets without an ID column.
        seen=[]; attempts=0
        def get(url,**_):
            nonlocal attempts
            p=urlsplit(url)
            if p.path!="/rows":
                return response(png({"train0":"red","train1":"blue","validation0":"green","validation1":"yellow"}[p.path[1:]]))
            q=parse_qs(p.query);split=q["split"][0];offset=int(q["offset"][0])
            attempts+=1
            if attempts==1:return response({},429,**{"Retry-After":"0"})
            self.assertEqual(q["length"],["1"])
            seen.append((split,offset))
            row=dict(image={"src":"/"+split+str(offset)},width=20,height=10,
                     objects=dict(bbox=[[2,1,8,4]],category=[0]))
            feature=dict(name="objects",type={"category":{"_type":"List","feature":{"_type":"ClassLabel","names":["sheep"]}}})
            return response(dict(features=[feature],partial=False,num_rows_total=9,
                                 rows=[dict(row_idx=offset,truncated_cells=[],row=row)]),revision="dataset-1")
        counts=self.ingest(spec,get,limit=2)
        self.assertEqual(seen,[("train",0),("train",1),("validation",0),("validation",1)])
        self.assertEqual(counts["train"],{"images":2,"boxes":2})
        self.assertEqual(json.loads((self.out/"source.json").read_text(encoding="utf-8"))["limit_per_split"],2)

    def test_hf_rejects_ambiguous_or_incomplete_rows(self):
        spec=json.loads((Path(__file__).parent/"examples/hf-sheep.json").read_text(encoding="utf-8"))
        for mode in ("truncated","partial","missing-row","stale-revision","class-mismatch","dimensions"):
            with self.subTest(mode=mode):
                def get(url,**_):
                    if urlsplit(url).path!="/rows":return response(png("green"))
                    row=dict(image_id=5,image={"src":"/asset"},width=21,height=10,
                             objects=dict(bbox=[[2,1,8,4]],category=[0]))
                    data=dict(num_rows_total=1,partial=mode=="partial",
                              features=[dict(name="objects",type={"category":{"_type":"ClassLabel","names":["wrong" if mode=="class-mismatch" else "sheep"]}})],
                              rows=[dict(row_idx=0,truncated_cells=["objects"] if mode=="truncated" else [],row=row)])
                    if mode=="missing-row":data["rows"]=[]
                    return response(data,revision="new")
                config=dict(spec, revision="old") if mode=="stale-revision" else spec
                message={"truncated":"truncated","partial":"Incomplete","missing-row":"Incomplete","stale-revision":"revision",
                         "class-mismatch":"ClassLabel","dimensions":"dimensions"}[mode]
                with self.assertRaisesRegex(ValueError,message):
                    self.ingest(config,get)
                self.assertFalse(self.out.exists())
