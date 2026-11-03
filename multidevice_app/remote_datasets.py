"""Read detection samples from the Hugging Face Dataset Viewer or a paginated REST API."""
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import requests
from PIL import Image

from .datasets import Sample, SPLITS, write_dataset
from .model_profile import validate_classes

HF_API = "https://datasets-server.huggingface.co"
JSON_LIMIT = 8 * 1024 * 1024
IMAGE_LIMIT = 32 * 1024 * 1024
RETRYABLE = {429, 500, 502, 503, 504}
EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp", "TIFF": ".tif"}
DEFAULT_FIELDS = {"id": "id", "image": "image", "boxes": "objects.bbox", "labels": "objects.category"}


def origin(url):
    if not isinstance(url, str):
        raise ValueError("Expected an HTTP URL")
    p = urlsplit(url)
    loopback = p.hostname in {"localhost", "127.0.0.1", "::1"}
    if (not p.hostname or p.username is not None or p.password is not None or p.fragment
            or not (p.scheme == "https" or (p.scheme == "http" and loopback))):
        raise ValueError("Use HTTPS (HTTP is allowed for loopback only), without credentials or fragments")
    return p.scheme, p.hostname, p.port if p.port is not None else (443 if p.scheme == "https" else 80)


class Client:
    def __init__(self, base, asset_origins, token):
        self.base = base
        self.api_origin = origin(base)
        self.assets = {self.api_origin, *(origin(u) for u in asset_origins)}
        self.token = token
        self.session = requests.Session()
        # Prevent ambient .netrc credentials from being used for an asset host.
        self.session.trust_env = False

    def fetch(self, url, *, asset=False):
        allowed = self.assets if asset else {self.api_origin}
        limit = IMAGE_LIMIT if asset else JSON_LIMIT
        for _ in range(6):
            host = origin(url)
            if host not in allowed:
                raise ValueError(f"Remote source did not allow this host: {host[1]}")
            headers = {}
            if self.token and host == self.api_origin:
                headers["Authorization"] = "Bearer " + self.token
            for attempt in range(3):
                try:
                    # This transport authenticates by Bearer only, including across redirects.
                    self.session.cookies.clear()
                    with self.session.get(url, headers=headers, stream=True, allow_redirects=False,
                                          timeout=(5, 20)) as response:
                        if response.is_redirect:
                            url = urljoin(url, response.headers["Location"])
                            break
                        if response.status_code in RETRYABLE and attempt < 2:
                            wait = response.headers.get("Retry-After")
                            if wait and (not wait.isdecimal() or int(wait) > 30):
                                raise ValueError(f"HTTP {response.status_code} from {host[1]}; retry later")
                            time.sleep(float(wait) if wait else 0.25 * 2**attempt)
                            continue
                        if not 200 <= response.status_code < 300:
                            raise ValueError(f"HTTP {response.status_code} from {host[1]}")
                        result = bytearray()
                        for chunk in response.iter_content(65536):
                            if len(result) + len(chunk) > limit:
                                raise ValueError(f"Remote response exceeds {limit} bytes")
                            result.extend(chunk)
                        return bytes(result), response.headers.get("X-Revision"), url
                except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError):
                    if attempt == 2:
                        raise ValueError(f"Remote read failed after 3 attempts from {host[1]}") from None
                    time.sleep(0.25 * 2**attempt)
                except requests.RequestException:
                    raise ValueError(f"Remote request failed at {host[1]}") from None
        raise ValueError("Too many HTTP redirects")


def field(row, path):
    def at(value, keys):
        if not keys:
            return value
        if isinstance(value, list):
            return [at(item, keys) for item in value]
        if not isinstance(value, dict) or keys[0] not in value:
            raise ValueError(f"Missing row field: {path}")
        return at(value[keys[0]], keys[1:])
    return at(row, path.split("."))


class Source:
    def __init__(self, config):
        self.provider = config["provider"]
        if self.provider not in ("huggingface", "http"):
            raise ValueError("Unknown remote provider")
        self.classes = validate_classes(config["classes"])
        ids = config.get("category_ids", list(range(len(self.classes))))
        if (not isinstance(ids, list) or len(ids) != len(self.classes)
                or any(type(k) is not int for k in ids) or len(set(ids)) != len(ids)):
            raise ValueError("category_ids must have one unique integer per class")
        self.ids = {key: i for i, key in enumerate(ids)}
        defaults = dict(DEFAULT_FIELDS)
        if self.provider == "huggingface":
            defaults["id"] = "$row_idx"
        self.fields = dict(defaults, **config.get("fields", {}))
        if (not self.fields.keys() <= (DEFAULT_FIELDS.keys() | {"width", "height"})
                or any(not isinstance(p, str) or not all(p.split(".")) for p in self.fields.values())
                or ("width" in self.fields) != ("height" in self.fields)):
            raise ValueError("Invalid row field mapping; width and height must be specified together")
        self.format = config["bbox_format"]
        if self.format not in ("xywh_pixels", "xyxy_pixels", "cxcywh_normalized"):
            raise ValueError("Unsupported bbox_format")
        self.splits = config["splits"]
        if (not isinstance(self.splits, dict) or not {"train", "val"} <= self.splits.keys() <= set(SPLITS)
                or any(not isinstance(v, str) or not v.strip() for v in self.splits.values())
                or len(set(self.splits.values())) != len(self.splits)):
            raise ValueError("Map distinct remote splits to train, val and optionally test")
        self.revision = config.get("revision")
        self.read_page = False
        if self.revision is not None and (not isinstance(self.revision, str) or not self.revision.strip()):
            raise ValueError("revision must be a nonempty string")
        self.info = {"provider": self.provider, "revision": self.revision, "bbox_format": self.format,
                     "fields": self.fields, "category_ids": ids}
        env = config.get("token_env")
        token = os.environ.get(env) if env else None
        if env and not token:
            raise ValueError("Set the configured token environment variable first")
        assets = config.get("asset_origins", [])
        if not isinstance(assets, list) or not all(isinstance(v, str) for v in assets):
            raise ValueError("asset_origins must be a list of origins")
        if self.provider == "huggingface":
            self.dataset, self.subset = config["dataset"], config.get("config", "default")
            self.page_size = config.get("page_size", 50)
            if (not all(isinstance(v, str) and v.strip() for v in (self.dataset, self.subset))
                    or type(self.page_size) is not int or not 1 <= self.page_size <= 100):
                raise ValueError("Invalid Hugging Face dataset, config or page_size (1-100)")
            base = HF_API
            self.info.update(dataset=self.dataset, config=self.subset, splits=self.splits)
        else:
            base = config["base_url"]
            p = urlsplit(base)
            if p.query:
                raise ValueError("Keep query parameters in split URLs, not base_url")
            paging = config.get("pagination", {})
            if not isinstance(paging, dict) or not paging.keys() <= {"items", "next", "cursor_param"}:
                raise ValueError("Invalid pagination settings")
            self.items_field = paging.get("items", "items")
            self.next_field = paging.get("next", "next")
            self.cursor_param = paging.get("cursor_param")
            if any(not isinstance(s, str) or not all(s.split(".")) for s in (self.items_field, self.next_field)):
                raise ValueError("Expected paths for page items and continuation")
            if self.cursor_param is not None and (not isinstance(self.cursor_param, str) or not self.cursor_param.strip()):
                raise ValueError("Expected a cursor query parameter name")
            self.info.update(api_origin=f"{p.scheme}://{p.netloc}", splits=list(self.splits))
        self.client = Client(base, assets, token)

    def page(self, url):
        data, revision, final_url = self.client.fetch(url)
        if (self.provider == "huggingface" or self.revision is not None) and not revision:
            raise ValueError("Dataset response is missing X-Revision")
        if (self.read_page or self.revision is not None) and revision != self.revision:
            raise ValueError("Remote dataset revision changed or differs from the configured revision")
        self.read_page = True
        self.revision = self.info["revision"] = revision
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError("Expected a JSON object for a page")
        return result, final_url

    def hf_classes(self, features):
        # ClassLabel metadata is optional. When available, verify its ID meanings.
        node = {f["name"]: f["type"] for f in features}
        for key in [*self.fields["labels"].split("."), None]:
            while isinstance(node, dict) and node.get("_type") in ("List", "LargeList", "Sequence"):
                node = node.get("feature")
            if key is not None:
                node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict) and node.get("_type") == "ClassLabel":
            names = node["names"]
            declared = {key: self.classes[index] for key, index in self.ids.items()}
            if not isinstance(names, list) or dict(enumerate(names)) != declared:
                raise ValueError("Hugging Face ClassLabel metadata differs from the configured class IDs/names")

    def http_rows(self, split, limit):
        url = urljoin(self.client.base.rstrip("/") + "/", split)
        visited = set()
        count = 0
        while url is not None:
            if url in visited:
                raise ValueError("Repeated API page URL")
            visited.add(url)
            page, final_url = self.page(url)
            items = field(page, self.items_field)
            continuation = field(page, self.next_field)
            if not isinstance(items, list) or (continuation is not None and
                    (not isinstance(continuation, str) or not continuation.strip())):
                raise ValueError("Expected an items array and a string continuation or null")
            if not items and continuation is not None:
                raise ValueError("Empty API page has a continuation but made no progress")
            for row in items:
                yield row, final_url, None
                count += 1
                if limit is not None and count >= limit:
                    return
            if continuation is None:
                return
            if self.cursor_param is None:
                url = urljoin(final_url, continuation)
            else:
                parsed = urlsplit(final_url)
                params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != self.cursor_param]
                params.append((self.cursor_param, continuation))
                url = parsed._replace(query=urlencode(params)).geturl()

    def hf_rows(self, split, limit):
        offset, total = 0, None
        while total is None or offset < total:
            length = self.page_size if limit is None else min(self.page_size, limit - offset)
            if not length:
                return
            query = urlencode(dict(dataset=self.dataset, config=self.subset, split=split, offset=offset, length=length))
            page, final_url = self.page(HF_API + "/rows?" + query)
            size = page["num_rows_total"]
            if type(size) is not int or size < 0 or (total is not None and size != total) or page.get("partial") is True:
                raise ValueError("Incomplete or changing Hugging Face split")
            total = size
            entries = page["rows"]
            if not isinstance(entries, list) or len(entries) != min(length, total - offset):
                raise ValueError("Incomplete Hugging Face page")
            self.hf_classes(page["features"])
            for entry in entries:
                if entry["row_idx"] != offset or entry.get("truncated_cells"):
                    raise ValueError("Unexpected row index or truncated Hugging Face row")
                yield entry["row"], final_url, offset
                offset += 1

    def class_id(self, raw):
        if type(raw) is int and raw in self.ids:
            return self.ids[raw]
        if isinstance(raw, str) and raw in self.classes:
            return self.classes.index(raw)
        raise ValueError(f"Unknown category in remote row: {raw!r}")

    def boxes(self, row, width, height):
        boxes = field(row, self.fields["boxes"])
        labels = field(row, self.fields["labels"])
        if not isinstance(boxes, list) or not isinstance(labels, list) or len(boxes) != len(labels):
            raise ValueError("Row bbox and category arrays differ")
        result = []
        for raw, label in zip(boxes, labels):
            if not isinstance(raw, list) or len(raw) != 4 or not all(
                type(v) in (int, float) and math.isfinite(v) for v in raw
            ):
                raise ValueError("Expected four finite bbox coordinates")
            x, y, w, h = raw
            if self.format == "xyxy_pixels":
                x1, y1, x2, y2 = x, y, w, h
            elif self.format == "xywh_pixels":
                x1, y1, x2, y2 = x, y, x + w, y + h
            else:
                x1, x2 = (x - w / 2) * width, (x + w / 2) * width
                y1, y2 = (y - h / 2) * height, (y + h / 2) * height
            x1, y1, x2, y2 = max(x1, 0), max(y1, 0), min(x2, width), min(y2, height)
            if x2 <= x1 or y2 <= y1:
                raise ValueError("Empty or out-of-image bbox")
            result.append((self.class_id(label), (x1 + x2) / (2 * width), (y1 + y2) / (2 * height),
                           (x2 - x1) / width, (y2 - y1) / height))
        return result

    def samples(self, split, limit, cache):
        ids = set()
        rows = self.http_rows if self.provider == "http" else self.hf_rows
        for row, page_url, row_index in rows(self.splits[split], limit):
            key = (row_index if self.provider == "huggingface" and self.fields["id"] == "$row_idx"
                   else field(row, self.fields["id"]))
            if type(key) not in (int, str) or key == "" or key in ids:
                raise ValueError(f"{split}: repeated or invalid row ID")
            ids.add(key)
            try:
                image = field(row, self.fields["image"])
                url = image.get("src") if isinstance(image, dict) else image
                if not isinstance(url, str) or not url.strip():
                    raise ValueError("Missing image URL")
                data, _, _ = self.client.fetch(urljoin(page_url, url), asset=True)
                with Image.open(BytesIO(data)) as im:
                    suffix = EXTENSIONS.get(im.format)
                    if not suffix or im.width * im.height > 40_000_000:
                        raise ValueError("Unsupported or oversized image")
                    width, height = im.size
                    im.load()
                if "width" in self.fields:
                    expected = (field(row, self.fields["width"]), field(row, self.fields["height"]))
                    if not all(type(v) is int for v in expected) or expected != (width, height):
                        raise ValueError(f"Annotation dimensions {expected} differ from image {(width, height)}")
                boxes = self.boxes(row, width, height)
                # Content-based names also let the common writer detect split leakage.
                path = cache / (hashlib.sha256(data).hexdigest() + suffix)
                path.write_bytes(data)
                try:
                    yield Sample(path, boxes)
                finally:
                    path.unlink(missing_ok=True)
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise ValueError(f"{split}, row {key!r}: {exc}") from None


def import_remote(config_path, output, limit=None):
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("--limit-per-split must be positive")
    config = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or type(config.get("schema_version")) is not int or config["schema_version"] != 1:
        raise ValueError("Expected remote source schema_version=1")
    if output.exists():
        raise ValueError(f"Choose a new output directory: {output}")
    source = Source(config)
    source.info["limit_per_split"] = limit
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output.parent, prefix=".remote-") as tmp:
            splits = {s: source.samples(s, limit, Path(tmp)) for s in source.splits}
            return write_dataset(output, source.classes, splits, source_info=source.info)
    finally:
        source.client.session.close()
