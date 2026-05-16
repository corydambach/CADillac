"""
Orchestrator: queries DB, builds job files, calls Blender 2.79 to render.
Generates anchor/positive/negative triplet groups for embedding network training.

Usage:
    python orchestrate.py --db collections_v1.db --count 5 --road_texture_dir textures/road
"""

import os
import os.path as op
import sys
import json
import sqlite3
import subprocess
import argparse
import time
from random import uniform, choice
from glob import glob
from math import sqrt

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow required. Run: pip install Pillow")
    sys.exit(1)

try:
    import cv2
    import numpy as np
except ImportError:
    print("ERROR: OpenCV required. Run: pip install opencv-python")
    sys.exit(1)

OUTPUT_SIZE = 32
ROLES = ['anchor', 'positive', 'negative_orientation', 'negative_identity', 'negative_translation']


def post_process_renders(work_dir):
    raw_files = sorted(glob(op.join(work_dir, '*-?_raw.png')))
    if not raw_files:
        return

    for raw_path in raw_files:
        base = op.basename(raw_path).replace('_raw.png', '')
        json_path = op.join(work_dir, base + '.json')
        final_path = op.join(work_dir, base + '.png')

        if not op.exists(json_path):
            continue

        with open(json_path) as f:
            meta = json.load(f)

        sat_azimuth = meta['sat_azimuth']

        img = cv2.imread(raw_path, cv2.IMREAD_UNCHANGED)
        h, w = img.shape[:2]

        if abs(sat_azimuth) > 0.1:
            center = (w / 2.0, h / 2.0)
            M = cv2.getRotationMatrix2D(center, sat_azimuth, 1.0)
            img = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LANCZOS4)

        # Crop center to output size
        left = (w - OUTPUT_SIZE) // 2
        top = (h - OUTPUT_SIZE) // 2
        img = img[top:top + OUTPUT_SIZE, left:left + OUTPUT_SIZE]

        cv2.imwrite(final_path, img)
        os.remove(raw_path)
        print("    Aligned: %s (sat_az=%.1f)" % (op.basename(final_path), sat_azimuth))


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


def build_job(model, job_id, num_per_session, role, group_id,
              car_azimuth, car_x=0.0, car_y=0.0, road_texture=None):
    vehicle = dict(model)
    vehicle['x'] = float(car_x)
    vehicle['y'] = float(car_y)

    job = {
        'job_id': job_id,
        'group_id': group_id,
        'role': role,
        'num_per_session': num_per_session,
        'car_azimuth': float(car_azimuth),
        'logging': 20,
        'vehicles': [vehicle],
    }

    if road_texture:
        job['road_texture'] = road_texture

    return job


def pick_different_model(models, current_index):
    if len(models) < 2:
        return models[current_index]
    idx = (current_index + 1) % len(models)
    return models[idx]


def pick_road_texture(road_textures):
    if not road_textures:
        return None
    return choice(road_textures)


def random_translation(min_m, max_m):
    dx = uniform(-max_m, max_m)
    dy = uniform(-max_m, max_m)
    d = sqrt(dx * dx + dy * dy)

    if d < 1e-6:
        dx, dy = min_m, 0.0
    elif d < min_m:
        s = min_m / d
        dx *= s
        dy *= s
    elif d > max_m:
        s = max_m / d
        dx *= s
        dy *= s

    return dx, dy


def build_group_jobs(model, model_index, models, job_id, group_id, args,
                     road_texture=None):
    car_azimuth = uniform(0, 360)
    neg_azimuth = (car_azimuth + args.orientation_delta) % 360
    neg_model = pick_different_model(models, model_index)
    bg_dx, bg_dy = random_translation(args.bg_shift_min_m, args.bg_shift_max_m)

    jobs = [
        build_job(model, job_id, args.num_per_session,
                  'anchor', group_id, car_azimuth,
                  road_texture=road_texture),

        build_job(model, job_id + 1, args.num_per_session,
                  'positive', group_id, car_azimuth,
                  road_texture=road_texture),

        build_job(model, job_id + 2, args.num_per_session,
                  'negative_orientation', group_id, neg_azimuth,
                  road_texture=road_texture),

        build_job(neg_model, job_id + 3, args.num_per_session,
                  'negative_identity', group_id, car_azimuth,
                  road_texture=road_texture),

        build_job(model, job_id + 4, args.num_per_session,
                  'negative_translation', group_id, car_azimuth,
                  car_x=bg_dx, car_y=bg_dy,
                  road_texture=road_texture),
    ]

    return jobs


def run_job(job, blender_path, script_path, work_dir):
    os.makedirs(work_dir, exist_ok=True)

    job_filename = 'job_%06d.json' % job['job_id']
    job_path = op.join(work_dir, job_filename)
    with open(job_path, 'w') as f:
        json.dump(job, f, indent=2)

    env = os.environ.copy()
    env['WORK_DIR_OVERRIDE'] = work_dir
    env['JOB_FILENAME'] = job_filename

    cmd = [blender_path, '--background', '--python', script_path]
    print("  Running: %s" % ' '.join(cmd))
    result = subprocess.run(cmd, env=env, capture_output=False, text=True)

    if result.stdout:
        print(result.stdout)

    if result.returncode != 0:
        print("  ERROR (return code %d)" % result.returncode)
        print("  stderr: %s" % result.stderr[-500:] if result.stderr else "")
    else:
        print("  OK")
        post_process_renders(work_dir)

    if op.exists(job_path):
        os.remove(job_path)

    return result.returncode


def collect_manifest(work_dir):
    json_files = sorted(glob(op.join(work_dir, '*.json')))
    entries = []
    for jf in json_files:
        png_path = jf.replace('.json', '.png')
        if not op.exists(png_path):
            continue
        with open(jf) as f:
            meta = json.load(f)
        meta['filename'] = op.basename(png_path)
        entries.append(meta)
    return entries


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--blender', default=r'E:\Downloads\blender-2.79b-windows64\blender.exe')
    parser.add_argument('--script', default=r'D:\Proj\study\cadillac\CADillac\render\photoSession_overhead.py')
    parser.add_argument('--work_dir', default=r'D:\tmp\cadillac_renders')
    parser.add_argument('--count', type=int, default=5)
    parser.add_argument('--sessions_per_model', type=int, default=1)
    parser.add_argument('--num_per_session', type=int, default=1)
    parser.add_argument('--orientation_delta', type=float, default=90.0)
    parser.add_argument('--bg_shift_min_m', type=float, default=0.6)
    parser.add_argument('--bg_shift_max_m', type=float, default=1.5)
    parser.add_argument('--road_texture_dir', default=None)
    parser.add_argument('--clause', default='WHERE error IS NULL AND dims_L IS NOT NULL')
    args = parser.parse_args()

    print("Querying database...")
    models = get_models(args.db, args.clause)
    print("Found %d models" % len(models))

    if args.count < len(models):
        models = models[:args.count]

    os.makedirs(args.work_dir, exist_ok=True)

    road_textures = []
    if args.road_texture_dir:
        road_textures = sorted(glob(op.join(args.road_texture_dir, '*.*')))
        print("Found %d road textures" % len(road_textures))

    print("\nRendering %d models, %d sessions each, %d roles per group..." % (
        len(models), args.sessions_per_model, len(ROLES)))
    print("Total jobs: %d" % (len(models) * args.sessions_per_model * len(ROLES)))

    successes = 0
    failures = 0
    job_id = 0

    for i, model in enumerate(models):
        for j in range(args.sessions_per_model):
            group_id = i * args.sessions_per_model + j

            print("\n[%d/%d] %s (%s)  session %d/%d" % (
                i + 1, len(models), model['model_id'], model['color'],
                j + 1, args.sessions_per_model))

            road_texture = pick_road_texture(road_textures)

            jobs = build_group_jobs(model, i, models, job_id, group_id, args,
                                   road_texture=road_texture)

            for job in jobs:
                print("  group=%06d  role=%-25s  job=%06d" % (
                    group_id, job['role'], job['job_id']))

                ret = run_job(job, args.blender, args.script, args.work_dir)

                if ret == 0:
                    successes += 1
                else:
                    failures += 1

            job_id += len(jobs)

    manifest = collect_manifest(args.work_dir)
    manifest_path = op.join(args.work_dir, 'manifest.json')
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    print("\nManifest: %s (%d entries)" % (manifest_path, len(manifest)))

    print("\n" + "=" * 40)
    print("Done. %d succeeded, %d failed." % (successes, failures))