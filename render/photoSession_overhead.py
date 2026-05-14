"""
Overhead rendering script for Blender 2.79.
Simulates orthorectified satellite imagery at varying ONA and azimuth.

Car orientation and position are read from the job file.
Each snapshot varies satellite viewing azimuth, off-nadir angle, and lighting.
Renders oversized, then the orchestrator rotates to north-up and crops.
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

JOB_INFO_NAME = 'job_info.json'

# ── RENDERING PARAMS ────────────────────────────────────────────────
PIXEL_SIZE_METERS = 0.30
OUTPUT_SIZE = 32
RENDER_SIZE = int(ceil(OUTPUT_SIZE * sqrt(2))) + 2
CAMERA_DIST = 50.0

SUN_ALTITUDE_MIN = 30
SUN_ALTITUDE_MAX = 90


def get_blend_path(file_id):
    path = atcadillac(op.join('blend', '%s.blend' % file_id))
    assert op.exists(path), "Blend not found: %s" % path
    return path


# ── CAR IMPORT ──────────────────────────────────────────────────────
def import_blend_car(blend_path, model_id, car_name=None):
    with bpy.data.libraries.load(blend_path, link=False) as (data_src, data_dst):
        data_dst.objects = [model_id]
    obj = data_dst.objects[0]
    assert obj is not None, "Failed to load object %s from %s" % (model_id, blend_path)
    bpy.context.scene.objects.link(obj)
    if car_name:
        obj.name = car_name
    return obj


# ── CAMERA ──────────────────────────────────────────────────────────
def setup_camera(ona_deg):
    """Camera fixed in +X tilt direction. Only ONA changes.
    Azimuth is handled by rotating the scene before render."""
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


# ── MAIN RENDERING ──────────────────────────────────────────────────
def render_session(job):
    vehicles = job['vehicles']

    # Open base scene
    bpy.context.user_preferences.filepaths.use_relative_paths = False
    assert op.exists(SCENE_PATH), "Scene not found: %s" % SCENE_PATH
    bpy.ops.wm.open_mainfile(filepath=SCENE_PATH)

    # Deselect all objects to avoid access violations in headless mode
    bpy.ops.object.select_all(action='DESELECT')

    # Set resolution (oversized for post-rotation crop)
    bpy.context.scene.render.resolution_x = RENDER_SIZE
    bpy.context.scene.render.resolution_y = RENDER_SIZE

    # Disable mist
    bpy.data.worlds['World'].mist_settings.use_mist = False

    # Mute compositor file output nodes
    if bpy.context.scene.node_tree:
        for node in bpy.context.scene.node_tree.nodes:
            if node.type == 'OUTPUT_FILE':
                node.mute = True

    # Read car azimuth from job
    car_azimuth = job['car_azimuth']
    car_az_rad = car_azimuth * pi / 180

    # Build output prefix from job metadata
    group_id = job.get('group_id', 0)
    role = job.get('role', 'unknown')
    model_id = vehicles[0]['model_id']
    file_id = vehicles[0]['file_id']
    out_prefix = 'g%06d_%s_%s' % (group_id, file_id, role)

    # Import vehicles and apply position + heading from job
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

    # Force full scene update after import to initialize depsgraph
    bpy.context.scene.update()

    # Hide building
    bpy.data.objects['-Building'].hide_render = True

    # Materials
    for m in bpy.data.materials:
        m.use_transparent_shadows = True
        m.ambient = 0.5

    # Ground setup
    ground_obj = bpy.data.objects['-Ground']
    ground_obj.dimensions.x = 12
    ground_obj.dimensions.y = 12
    for mat in ground_obj.data.materials:
        if mat:
            mat.diffuse_intensity = 0.5

    # Road texture — from job if specified, otherwise random
    if job.get('road_texture'):
        bpy.data.images['ground'].filepath = job['road_texture']
    else:
        road_textures = glob(op.join(ROAD_TEXTURE_DIR, '*.*'))
        if road_textures:
            bpy.data.images['ground'].filepath = choice(road_textures)

    # Objects to rotate for satellite azimuth simulation
    scene_objects = car_names + ['-Ground']

    # Render each snapshot
    for i in range(job['num_per_session']):
        # Random satellite viewing parameters
        sat_azimuth = uniform(low=0, high=360)
        ona = uniform(low=0, high=20)
        sun_azimuth = uniform(low=0, high=360)
        sun_altitude = uniform(low=SUN_ALTITUDE_MIN, high=SUN_ALTITUDE_MAX)
        weather = choice(['Sunny', 'Cloudy', 'Sunny', 'Sunny'])

        # Camera: only ONA changes
        setup_camera(ona)

        # Weather and sun
        set_weather({'weather': weather, 'sun_altitude': sun_altitude, 'sun_azimuth': sun_azimuth})

        # Clamp energy values to avoid negative values from normal() causing segfaults
        sun_obj = bpy.data.objects.get('-Sun')
        if sun_obj and sun_obj.data:
            sun_obj.data.energy = max(0.1, sun_obj.data.energy)
        world = bpy.data.worlds.get('World')
        if world:
            world.light_settings.environment_energy = max(0.1, world.light_settings.environment_energy)

        # Rotate scene for satellite azimuth
        sat_az_rad = sat_azimuth * pi / 180
        for name in scene_objects:
            obj = bpy.data.objects[name]
            if obj.data:
                obj.data.transform(Matrix.Rotation(-sat_az_rad, 4, 'Z'))
            else:
                obj.rotation_euler[2] -= sat_az_rad

        bpy.context.scene.update()

        # Debug: log actual rotation values at render time
        for name in scene_objects:
            obj = bpy.data.objects[name]
            logging.info("PRE-RENDER %s rot_z=%.3f", name, obj.rotation_euler[2])

        # Render
        raw_path = op.join(WORK_DIR, '%s_%03d_raw.png' % (out_prefix, i))
        bpy.data.scenes['Scene'].render.filepath = raw_path

        logging.info("Rendering %s_%03d  ONA=%.1f  sat_az=%.1f  car_az=%.1f",
                     out_prefix, i, ona, sat_azimuth, car_azimuth)
        try:
            bpy.ops.render.render(write_still=True)
        except Exception as e:
            logging.error("Render error: %s", str(e))

        # Undo scene rotation
        for name in scene_objects:
            obj = bpy.data.objects[name]
            if obj.data:
                obj.data.transform(Matrix.Rotation(sat_az_rad, 4, 'Z'))
            else:
                obj.rotation_euler[2] += sat_az_rad

        # Write per-image metadata
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
        with open(op.join(WORK_DIR, '%s_%03d.json' % (out_prefix, i)), 'w') as f:
            json.dump(out_info, f, indent=2)

        logging.info("Snapshot %d done", i)


# ── ENTRY POINT ─────────────────────────────────────────────────────
WORK_DIR = os.getenv('WORK_DIR_OVERRIDE')
if not WORK_DIR:
    raise Exception("Set WORK_DIR_OVERRIDE environment variable")

job_path = op.join(WORK_DIR, os.getenv('JOB_FILENAME', JOB_INFO_NAME))
assert op.exists(job_path), "Job file not found: %s" % job_path

job = json.load(open(job_path))
logging.basicConfig(level=job.get('logging', 20), stream=sys.stderr,
                    format='%(levelname)s:overhead: %(message)s')

try:
    render_session(job)
except Exception as e:
    logging.error("FATAL: %s", str(e))
    logging.error(traceback.format_exc())