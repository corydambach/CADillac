"""
Orchestrator for classification renders: generates all jobs up front,
writes them to a single job list file, then invokes Blender once.

Blender processes every job sequentially (reloading the base scene each time),
producing 4 views per vehicle: top, side, front, rear.

Usage:
    python orchestrate_classify.py --db collections_v1.db --count 5
"""

import os
import os.path as op
import sys
import json
import sqlite3
import subprocess
import argparse
from glob import glob

OUTPUT_SIZE = 32

def get_models(db_path, clause="WHERE error IS NULL AND dims_L IS NOT NULL", limit=None):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    q = "SELECT model_id, collection_id, dims_L, dims_W, dims_H, color FROM cad %s ORDER BY id" % clause
    if limit:
        q += " LIMIT %d" % limit
    c.execute(q)
    rows = c.fetchall()

    q2 = "SELECT model_id, id FROM cad"
    c.execute(q2)
    id_map = {r[0]: r[1] for r in c.fetchall()}
    conn.close()

    models = []
    for r in rows:
        models.append({
            'model_id': r[0],
            'collection_id': r[1],
            'file_id': id_map.get(r[0], r[0]),
            'dims': {'x': r[2], 'y': r[3], 'z': r[4]},
            'color': r[5] or 'unknown',
        })
    return models


def build_jobs(models):
    """Build one job per vehicle. Each job requests all four views."""
    jobs = []
    for model in models:
        jobs.append({
            'model_id': model['model_id'],
            'file_id': model['file_id'],
            'color': model['color'],
            'views': ['top', 'side', 'front', 'rear'],
        })
    return jobs


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--blender', default=r'E:\Downloads\blender-2.79b-windows64\blender.exe')
    parser.add_argument('--script', default=r'D:\Proj\study\cadillac\CADillac\render\photoSession_multiple.py')
    parser.add_argument('--work_dir', default=r'D:\tmp\cadillac_classify')
    parser.add_argument('--count', type=int, default=None)
    parser.add_argument('--clause', default='WHERE error IS NULL AND dims_L IS NOT NULL')
    args = parser.parse_args()

    print("Querying database...")
    models = get_models(args.db, args.clause, limit=args.count)
    print("Found %d models" % len(models))

    os.makedirs(args.work_dir, exist_ok=True)

    jobs = build_jobs(models)
    print("Generated %d jobs (%d renders total)" % (len(jobs), len(jobs) * 4))

    jobs_path = op.join(args.work_dir, 'jobs.json')
    with open(jobs_path, 'w') as f:
        json.dump(jobs, f, indent=2)
    print("Wrote job list: %s" % jobs_path)

    env = os.environ.copy()
    env['WORK_DIR_OVERRIDE'] = args.work_dir

    cmd = [args.blender, '--background', '--python', args.script]
    print("\nLaunching Blender for all %d jobs..." % len(jobs))
    print("  %s" % ' '.join(cmd))

    result = subprocess.run(cmd, env=env, capture_output=False, text=True)

    if result.returncode != 0:
        print("\nBlender exited with code %d" % result.returncode)
        if result.stderr:
            print("  stderr (last 500 chars): %s" % result.stderr[-500:])
    else:
        print("\nBlender finished successfully.")

    # Count outputs
    pngs = glob(op.join(args.work_dir, '*.png'))
    print("Output images: %d" % len(pngs))
    print("Done.")