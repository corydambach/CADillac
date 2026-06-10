"""
Batch orchestrator: queries DB, builds ALL jobs up front, writes jobs.json,
invokes Blender once. Blender processes every job sequentially.

Generates anchor/positive/negative triplet groups for embedding network training.

Usage:
    python orchestrate_overhead_batch.py --db collections_v1.db --count 5 --road_texture_dir textures/road
"""

import os
import os.path as op
import sys
import json
import sqlite3
import subprocess
import argparse
from random import uniform, choice, seed as rand_seed
from glob import glob
from math import sqrt

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

    print("Post-processing %d raw renders..." % len(raw_files))
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

        left = (w - OUTPUT_SIZE) // 2
        top = (h - OUTPUT_SIZE) // 2
        img = img[top:top + OUTPUT_SIZE, left:left + OUTPUT_SIZE]

        cv2.imwrite(final_path, img)
        os.remove(raw_path)


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


def build_job(model, job_id, role, group_id, car_azimuth,
              sat_azimuth, ona, sun_azimuth, sun_altitude, weather,
              car_x=0.0, car_y=0.0, road_texture=None):
    vehicle = dict(model)
    vehicle['x'] = float(car_x)
    vehicle['y'] = float(car_y)

    return {
        'job_id':          job_id,
        'group_id':        group_id,
        'role':            role,
        'car_azimuth':     float(car_azimuth),
        'sat_azimuth':     float(sat_azimuth),
        'off_nadir_angle': float(ona),
        'sun_azimuth':     float(sun_azimuth),
        'sun_altitude':    float(sun_altitude),
        'weather':         weather,
        'road_texture':    road_texture,
        'logging':         20,
        'vehicles':        [vehicle],
    }


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


def sample_conditions(args):
    return {
        'sat_azimuth':     uniform(0, 360),
        'off_nadir_angle': uniform(0, 30),
        'sun_azimuth':     uniform(0, 360),
        'sun_altitude':    uniform(25, 90),
        'weather':         choice(['Sunny', 'Cloudy', 'Sunny', 'Sunny']),
    }


def build_group_jobs(model, model_index, models, job_id, group_id, args,
                     road_texture=None):
    car_azimuth = uniform(0, 360)
    neg_azimuth = (car_azimuth + args.orientation_delta) % 360
    neg_model = pick_different_model(models, model_index)
    bg_dx, bg_dy = random_translation(args.bg_shift_min_m, args.bg_shift_max_m)

    roles = [
        ('anchor',                 model,     car_azimuth, 0.0, 0.0),
        ('positive',               model,     car_azimuth, 0.0, 0.0),
        ('negative_orientation',   model,     neg_azimuth, 0.0, 0.0),
        ('negative_identity',      neg_model, car_azimuth, 0.0, 0.0),
        ('negative_translation',   model,     car_azimuth, bg_dx, bg_dy),
        ('negative_empty',         None,      0.0,         0.0, 0.0),
    ]

    jobs = []
    for role, mdl, az, dx, dy in roles:
        for sample_idx in range(args.num_per_session):
            cond = sample_conditions(args)
            vehicles = []
            if mdl is not None:
                v = dict(mdl)
                v['x'] = float(dx)
                v['y'] = float(dy)
                vehicles = [v]
            jobs.append({
                'job_id': job_id,
                'group_id': group_id,
                'role': role,
                'sample_idx': sample_idx,
                'car_azimuth': float(az),
                'vehicles': vehicles,
                'road_texture': road_texture,
                'logging': 20,
                **cond,
            })
            job_id += 1

    return jobs, job_id


def build_test_sun_jobs(models, args, road_texture=None):
    """Deterministic grid: sun azimuth x sat azimuth at fixed elevation.
    Shadow direction should track sun regardless of sat rotation."""
    sun_azimuths = list(range(0, 360, 45))
    sat_azimuths = [0, 90, 180, 270]
    model = models[0]

    jobs = []
    for job_id, (sat_az, sun_az) in enumerate(
        [(s, u) for s in sat_azimuths for u in sun_azimuths]
    ):
        jobs.append( build_job(
            model, job_id, 'anchor', job_id,
            car_azimuth=0.0,
            sat_azimuth=float(sat_az),
            ona=0.0,
            sun_azimuth=float(sun_az),
            sun_altitude=70.0,
            weather='Sunny',
            road_texture=road_texture,
        ))

    return jobs


def collect_manifest(work_dir):
    json_files = sorted(glob(op.join(work_dir, '*.json')))
    entries = []
    for jf in json_files:
        if op.basename(jf) in ('jobs.json', 'manifest.json'):
            continue
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
    parser.add_argument('--script', default=r'D:\Proj\study\cadillac\CADillac\render\photoSession_overhead_batch.py')
    parser.add_argument('--work_dir', default=r'D:\tmp\cadillac_renders')
    parser.add_argument('--count', required=True, type=int)
    parser.add_argument('--sessions_per_model', type=int, default=1)
    parser.add_argument('--num_per_session', type=int, default=1)
    parser.add_argument('--orientation_delta', type=float, default=90.0)
    parser.add_argument('--bg_shift_min_m', type=float, default=0.6)
    parser.add_argument('--bg_shift_max_m', type=float, default=1.5)
    parser.add_argument('--road_texture_dir', required=True)
    parser.add_argument('--clause', default='WHERE error IS NULL AND dims_L IS NOT NULL')
    parser.add_argument('--seed', type=int, default=None)
    parser.add_argument('--test_sun', action='store_true',
                    help='Generate deterministic sun/sat azimuth grid for verification')
    args = parser.parse_args()

    if args.seed is not None:
        rand_seed(args.seed)

    print("Querying database...")
    models = get_models(args.db, args.clause, limit=args.count)
    print("Found %d models" % len(models))

    os.makedirs(args.work_dir, exist_ok=True)

    road_textures = []
    if args.road_texture_dir:
        road_textures = sorted(glob(op.join(args.road_texture_dir, '*.*')))
        print("Found %d road textures" % len(road_textures))

    print("\nBuilding jobs: %d models x %d sessions x %d roles..." % (
        len(models), args.sessions_per_model, len(ROLES)))
    if args.test_sun:
        print("TEST MODE: generating sun verification grid...")
        road_texture = pick_road_texture(road_textures)
        all_jobs = build_test_sun_jobs(models, args, road_texture=road_texture)
    else:
        all_jobs = []
        job_id = 0
        for i, model in enumerate(models):
            for j in range(args.sessions_per_model):
                group_id = i * args.sessions_per_model + j
                road_texture = pick_road_texture(road_textures)
                [jobs, job_id] = build_group_jobs(model, i, models, job_id, group_id, args,
                                                  road_texture=road_texture)
                all_jobs.extend(jobs)

    print("Generated %d jobs total" % len(all_jobs))

    jobs_path = op.join(args.work_dir, 'jobs.json')
    with open(jobs_path, 'w') as f:
        json.dump(all_jobs, f, indent=2)
    print("Wrote job list: %s" % jobs_path)

    env = os.environ.copy()
    env['WORK_DIR_OVERRIDE'] = args.work_dir
    env['JOBS_FILENAME'] = 'jobs.json'

    cmd = [args.blender, '--background', '--python', args.script]
    print("\nLaunching Blender for all %d jobs..." % len(all_jobs))
    print("  %s" % ' '.join(cmd))

    result = subprocess.run(cmd, env=env, capture_output=False, text=True)

    if result.returncode != 0:
        print("\nBlender exited with code %d" % result.returncode)
        if result.stderr:
            print("  stderr (last 500 chars): %s" % result.stderr[-500:])
    else:
        print("\nBlender finished successfully.")

    print("\nPost-processing renders (rotate to north-up, center crop)...")
    post_process_renders(args.work_dir)

    manifest = collect_manifest(args.work_dir)
    manifest_path = op.join(args.work_dir, 'manifest.json')
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    print("\nManifest: %s (%d entries)" % (manifest_path, len(manifest)))

    pngs = [p for p in glob(op.join(args.work_dir, '*.png')) if '_raw' not in p]
    print("Final output images: %d" % len(pngs))
    print("Done.")