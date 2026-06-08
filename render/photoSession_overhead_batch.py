"""
Batch overhead rendering script for Blender 2.79.
Reads jobs.json (a list of jobs) from WORK_DIR_OVERRIDE and renders all of them
in a single Blender invocation. Reopens the base scene per job for clean state.
"""

import bpy
import sys
import os
import os.path as op
import json
import logging
import traceback
from math import pi, cos, sin, sqrt, ceil
from glob import glob
from random import choice
from numpy.random import uniform
from mathutils import Matrix
import numpy as np

sys.path.insert(0, op.dirname(op.dirname(os.path.realpath(__file__))))
from render.common import *
from render.renderUtil import atcadillac

# ── PATHS ───────────────────────────────────────────────────────────
if not os.getenv('CADILLAC_DATA_PATH'):
    raise Exception('Set CADILLAC_DATA_PATH environment variable')

SCENE_PATH       = op.join(op.dirname(os.path.realpath(__file__)), 'resources', 'photo-session.blend')
ROAD_TEXTURE_DIR = atcadillac('resources/textures/road')

# ── RENDERING PARAMS ────────────────────────────────────────────────
PIXEL_SIZE_METERS = 0.30
OUTPUT_SIZE = 32
RENDER_SIZE = int(ceil(OUTPUT_SIZE * sqrt(2))) + 2
CAMERA_DIST = 50.0

SUN_ALTITUDE_MIN = 30
SUN_ALTITUDE_MAX = 90

ROLE_SHORT = {
    'anchor': 'a',
    'positive': 'p',
    'negative_orientation': 'nr',
    'negative_identity': 'ni',
    'negative_translation': 'nt',
}


def get_blend_path(file_id):
    path = atcadillac(op.join('blend', '%s.blend' % file_id))
    assert op.exists(path), "Blend not found: %s" % path
    return path


def import_blend_car(blend_path, model_id, car_name=None):
    with bpy.data.libraries.load(blend_path, link=False) as (data_src, data_dst):
        data_dst.objects = [model_id]
    obj = data_dst.objects[0]
    assert obj is not None, "Failed to load object %s from %s" % (model_id, blend_path)
    bpy.context.scene.objects.link(obj)
    if car_name:
        obj.name = car_name
    return obj


def setup_camera(ona_deg):
    camera_obj = bpy.data.objects['-Camera']
    camera_data = camera_obj.data

    camera_data.type = 'ORTHO'
    camera_data.ortho_scale = PIXEL_SIZE_METERS * RENDER_SIZE

    ona_rad = ona_deg * pi / 180

    x = CAMERA_DIST * sin(ona_rad)
    y = 0
    z = CAMERA_DIST * cos(ona_rad)

    camera_obj.location = (x, y, z)
    camera_obj.rotation_euler = (ona_rad, 0, pi / 2)


def render_one_job(job, work_dir):
    """Render a single job. Assumes the base scene has just been opened."""
    vehicles = job['vehicles']

    bpy.ops.object.select_all(action='DESELECT')

    bpy.context.scene.render.resolution_x = RENDER_SIZE
    bpy.context.scene.render.resolution_y = RENDER_SIZE

    bpy.data.worlds['World'].mist_settings.use_mist = False

    if bpy.context.scene.node_tree:
        for node in bpy.context.scene.node_tree.nodes:
            if node.type == 'OUTPUT_FILE':
                node.mute = True

    car_azimuth = job['car_azimuth']
    car_az_rad = car_azimuth * pi / 180

    group_id = job.get('group_id', 0)
    role = job.get('role', 'unknown')
    model_id = vehicles[0]['model_id']
    out_prefix = 'g%d-%s' % (group_id, ROLE_SHORT.get(role, role))

    # Import vehicles
    car_names = []
    for i, vehicle in enumerate(vehicles):
        blend_path = get_blend_path(vehicle['file_id'])
        car_name = 'car-%d' % i
        car_names.append(car_name)
        import_blend_car(blend_path, vehicle['model_id'], car_name)

        obj = bpy.data.objects[car_name]
        obj.location.x += vehicle.get('x', 0.0)
        obj.location.y += vehicle.get('y', 0.0)
        obj.data.transform(Matrix.Rotation(car_az_rad, 4, 'Z'))

    bpy.context.scene.update()

    bpy.data.objects['-Building'].hide_render = True

    for m in bpy.data.materials:
        m.use_transparent_shadows = True
        m.ambient = 0.5

    ground_obj = bpy.data.objects['-Ground']
    ground_obj.dimensions.x = 12
    ground_obj.dimensions.y = 12
    for mat in ground_obj.data.materials:
        if mat:
            mat.diffuse_intensity = 0.5

    if job.get('road_texture'):
        bpy.data.images['ground'].filepath = job['road_texture']
    else:
        road_textures = glob(op.join(ROAD_TEXTURE_DIR, '*.*'))
        if road_textures:
            bpy.data.images['ground'].filepath = choice(road_textures)

    scene_objects = car_names + ['-Ground']

    for i in range(job['num_per_session']):
        sat_azimuth  = uniform(low=0, high=360)
        ona          = uniform(low=0, high=30)
        sun_azimuth  = uniform(low=0, high=360)
        sun_altitude = uniform(low=SUN_ALTITUDE_MIN, high=SUN_ALTITUDE_MAX)
        weather = choice(['Sunny', 'Cloudy', 'Sunny', 'Sunny'])

        setup_camera(ona)
        set_weather({'weather': weather, 'sun_altitude': sun_altitude, 'sun_azimuth': sun_azimuth})

        sat_az_rad = sat_azimuth * pi / 180
        for name in scene_objects:
            obj = bpy.data.objects[name]
            if obj.data:
                obj.data.transform(Matrix.Rotation(-sat_az_rad, 4, 'Z'))
            else:
                obj.rotation_euler[2] -= sat_az_rad

        bpy.context.scene.update()

        raw_path = op.join(work_dir, '%s-%d_raw.png' % (out_prefix, i))
        bpy.data.scenes['Scene'].render.filepath = raw_path

        logging.info("[job %d] Rendering %s-%d  ONA=%.1f  sat_az=%.1f  car_az=%.1f",
                     job.get('job_id', -1), out_prefix, i, ona, sat_azimuth, car_azimuth)
        try:
            bpy.ops.render.render(write_still=True)
        except Exception as e:
            logging.error("Render error: %s", str(e))

        # Undo scene rotation (in case anything later reads from it)
        for name in scene_objects:
            obj = bpy.data.objects[name]
            if obj.data:
                obj.data.transform(Matrix.Rotation(sat_az_rad, 4, 'Z'))
            else:
                obj.rotation_euler[2] += sat_az_rad

        out_info = {
            'group_id': group_id,
            'role': role,
            'model_id': model_id,
            'file_id': out_prefix,
            'color': vehicles[0].get('color', 'unknown'),
            'car_azimuth': float(car_azimuth),
            'car_x': float(vehicles[0].get('x', 0.0)),
            'car_y': float(vehicles[0].get('y', 0.0)),
            'sat_azimuth': float(sat_azimuth),
            'off_nadir_angle': float(ona),
            'pixel_size_m': PIXEL_SIZE_METERS,
            'weather': weather,
            'sun_azimuth': float(sun_azimuth),
            'sun_altitude': float(sun_altitude),
        }
        with open(op.join(work_dir, '%s-%d.json' % (out_prefix, i)), 'w') as f:
            json.dump(out_info, f, indent=2)


def run_batch(jobs, work_dir):
    bpy.context.user_preferences.filepaths.use_relative_paths = False
    assert op.exists(SCENE_PATH), "Scene not found: %s" % SCENE_PATH

    n = len(jobs)
    succeeded = 0
    failed = 0

    for idx, job in enumerate(jobs):
        logging.info("=" * 60)
        logging.info("[%d/%d] job_id=%s group=%s role=%s",
                     idx + 1, n,
                     job.get('job_id'), job.get('group_id'), job.get('role'))
        try:
            # Reopen base scene for clean state per job
            bpy.ops.wm.open_mainfile(filepath=SCENE_PATH)
            render_one_job(job, work_dir)
            succeeded += 1
        except Exception as e:
            logging.error("Job %s failed: %s", job.get('job_id'), str(e))
            logging.error(traceback.format_exc())
            failed += 1

    logging.info("=" * 60)
    logging.info("Batch complete: %d succeeded, %d failed", succeeded, failed)


# ── ENTRY POINT ─────────────────────────────────────────────────────
WORK_DIR = os.getenv('WORK_DIR_OVERRIDE')
if not WORK_DIR:
    raise Exception("Set WORK_DIR_OVERRIDE environment variable")

jobs_filename = os.getenv('JOBS_FILENAME', 'jobs.json')
jobs_path = op.join(WORK_DIR, jobs_filename)
assert op.exists(jobs_path), "Jobs file not found: %s" % jobs_path

with open(jobs_path) as f:
    jobs = json.load(f)

log_level = jobs[0].get('logging', 20) if jobs else 20
logging.basicConfig(level=log_level, stream=sys.stderr,
                    format='%(levelname)s:overhead_batch: %(message)s')

logging.info("Loaded %d jobs from %s", len(jobs), jobs_path)

try:
    run_batch(jobs, WORK_DIR)
except Exception as e:
    logging.error("FATAL: %s", str(e))
    logging.error(traceback.format_exc())