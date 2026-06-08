"""
Generate a sprite sheet from rendered multi-view thumbnails.
Each row contains 4 views for one collection: [top, side, front, rear].
Outputs a single PNG and a JSON index for clients.

Usage:
    python generate_spritesheet.py --render_dir D:\tmp\cadillac_classify --db collections_v1.db
"""

import argparse
import json
import os.path as op
import sqlite3
from math import ceil, sqrt

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow required. Run: pip install Pillow")
    exit(1)

VIEWS = ['top', 'side', 'front', 'rear']
COLS = len(VIEWS)  # always 4 columns


def get_collection_ids(db_path, clause):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    q = "SELECT id FROM cad %s ORDER BY id" % clause
    c.execute(q)
    ids = [r[0] for r in c.fetchall()]
    conn.close()
    return ids


def find_valid_collections(render_dir, collection_ids):
    """Return list of (cid, {view: path}) for collections that have all 4 views."""
    valid = []
    for cid in collection_ids:
        paths = {}
        for v in VIEWS:
            p = op.join(render_dir, '%s_%s.png' % (cid, v))
            if op.exists(p):
                paths[v] = p
        if len(paths) == COLS:
            valid.append((cid, paths))
    return valid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--render_dir', required=True)
    parser.add_argument('--db', required=True)
    parser.add_argument('--clause', default='WHERE error IS NULL AND dims_L IS NOT NULL')
    parser.add_argument('--tile_size', type=int, default=128)
    parser.add_argument('--output', default=None, help='Output path. Default: <render_dir>/spritesheet.png')
    args = parser.parse_args()

    collection_ids = get_collection_ids(args.db, args.clause)
    valid = find_valid_collections(args.render_dir, collection_ids)

    if not valid:
        print("No collections with all 4 views found in %s" % args.render_dir)
        return

    print("Found %d collections with all views" % len(valid))

    ts = args.tile_size
    block_w = COLS * ts  # one collection = 4 tiles wide
    blocks_per_row = max(1, ceil(sqrt(len(valid))))
    block_rows = ceil(len(valid) / blocks_per_row)
    width = blocks_per_row * block_w
    height = block_rows * ts

    print("Layout: %d blocks/row, %d rows (%d x %d px)" % (blocks_per_row, block_rows, width, height))

    sheet = Image.new('RGB', (width, height), (0, 0, 0))
    index = {}

    for i, (cid, paths) in enumerate(valid):
        bx = (i % blocks_per_row) * block_w
        by = (i // blocks_per_row) * ts

        for col, view in enumerate(VIEWS):
            img = Image.open(paths[view]).convert('RGB')
            if img.size != (ts, ts):
                img = img.resize((ts, ts), Image.NEAREST)
            sheet.paste(img, (bx + col * ts, by))

        index[cid] = {
            'row': i // blocks_per_row,
            'tiles': {v: {'col': c, 'x': bx + c * ts, 'y': by} for c, v in enumerate(VIEWS)},
        }

    out_path = args.output or op.join(args.render_dir, 'spritesheet.png')
    sheet.save(out_path, 'PNG', optimize=True, compress_level=9)
    print("Sprite sheet: %s (%.2f MB)" % (out_path, op.getsize(out_path) / (1024 * 1024)))

    index_path = out_path.replace('.png', '.json')
    meta = {
        'tileSize': ts,
        'cols': COLS,
        'blocksPerRow': blocks_per_row,
        'blockRows': block_rows,
        'imageWidth': width,
        'imageHeight': height,
        'views': VIEWS,
        'count': len(valid),
        'index': index,
    }
    with open(index_path, 'w') as f:
        json.dump(meta, f, indent=2)
    print("Index: %s (%d entries)" % (index_path, len(index)))


if __name__ == '__main__':
    main()