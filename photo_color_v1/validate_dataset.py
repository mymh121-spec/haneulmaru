"""Verify exported files, coordinate bounds, category mappings and source split isolation."""
import collections
import hashlib
import json
import math
import sys
from pathlib import Path
from PIL import Image


def validate(root):
    root = Path(root)
    rows = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    groups, pixels, names, hashes = collections.defaultdict(list), {}, {}, {}
    color_counts = collections.defaultdict(collections.Counter)
    for row in rows:
        assert row['marker_color'] in ('none', 'red', 'blue', 'green')
        groups[row['source_id']].append(row)
        for key, lookup in [('pixel_sha256', pixels), ('source_name', names), ('source_sha256', hashes)]:
            value = row[key]
            assert value not in lookup or lookup[value] == row['split'], f'Cross-split leakage: {key}'
            lookup[value] = row['split']
        image = root / row['image']
        assert hashlib.sha256(image.read_bytes()).hexdigest() == row['sha256'], f'Hash mismatch: {image}'
        with Image.open(image) as im:
            assert im.size == (row['width'], row['height'])
            im.verify()
        for field in ('drone_label', 'marker_label'):
            text = (root / row[field]).read_text().strip()
            if field == 'marker_label' and row['marker_color'] == 'none':
                assert not text
                continue
            parts = text.split()
            assert len(parts) == 5
            expected = 0 if field == 'drone_label' else ('red', 'blue', 'green').index(row['marker_color'])
            assert int(parts[0]) == expected
            cx, cy, w, h = map(float, parts[1:])
            assert all(math.isfinite(v) for v in (cx, cy, w, h))
            assert w > 0 and h > 0
            assert cx-w/2 >= -1e-7 and cy-h/2 >= -1e-7
            assert cx+w/2 <= 1+1e-7 and cy+h/2 <= 1+1e-7
        color_counts[row['split']][row['marker_color']] += 1
    for group in groups.values():
        assert len(group) == 4
        assert len({r['split'] for r in group}) == 1
        assert {r['marker_color'] for r in group} == {'none', 'red', 'blue', 'green'}
        tags = [r for r in group if r['marker_color'] != 'none']
        assert len({tuple(r['marker_bbox']) for r in tags}) == 1
    originals = [group[0] for group in groups.values()]
    for i, a in enumerate(originals):
        for b in originals[i+1:]:
            assert (a['dhash64'] ^ b['dhash64']).bit_count() > 4, 'Unfiltered near duplicate'
    for split in ('train', 'val', 'test'):
        coco = json.loads((root/'annotations'/f'{split}.coco.json').read_text(encoding='utf-8'))
        assert coco['categories'] == [{'id': 1, 'name': 'drone'}]
        matching = [r for r in rows if r['split'] == split]
        assert len(coco['images']) == len(matching) == len(coco['annotations'])
        lookup = {r['image']: r for r in matching}
        image_lookup = {im['id']: im for im in coco['images']}
        for ann in coco['annotations']:
            im = image_lookup[ann['image_id']]
            assert ann['category_id'] == 1
            assert ann['bbox'] == lookup[im['file_name']]['bbox']
    return {'status': 'PASS', 'images_verified': len(rows), 'source_groups': len(groups),
            'counts': dict(color_counts), 'source_group_leakage': 0,
            'all_original_pairs_dhash_distance_greater_than': 4,
            'note': 'Structural validation only; not a model accuracy or physical-marker benchmark.'}


if __name__ == '__main__':
    print(json.dumps(validate(Path(sys.argv[1]) if len(sys.argv)>1 else Path('.')), ensure_ascii=False, indent=2))
