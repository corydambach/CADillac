"""
Overhead rendering script for Blender 2.79.
Simulates orthorectified satellite imagery at varying ONA and azimuth.

Car orientation is fixed per session.
Each snapshot varies satellite viewing azimuth and off-nadir angle.
Renders oversized, then rotates to north-up and crops.
"""

import bpy
import sys
import os
import os.path as op
import json
import logging
from math import pi, cos, sin, sqrt, ceil
from glob import glob
from random import choice
from numpy.random import normal, uniform
import numpy as np
from mathutils import Vector

sys.path.insert(0, op.dirname(op.dirname(os.path.realpath(__file__)))) # = '..'
from render.common import *
from cads.collectionUtilities import getBlendPath
from render.renderUtil import atcadillac

# ── PATHS ───────────────────────────────────────────────────────────
if not os.getenv('CADILLAC_DATA_PATH'):
    raise Exception('Set CADILLAC_DATA_PATH environment variable')

def atcadillac(path):
    if op.isabs(path):
        return path
    return op.join(os.getenv('CADILLAC_DATA_PATH'), path)

SCENE_PATH       = op.join(op.dirname(os.path.realpath(__file__)), 'resources', 'photo-session.blend')
ROAD_TEXTURE_DIR = atcadillac('resources/textures/road')

JOB_INFO_NAME = 'job_info.json'

# ── RENDERING PARAMS ────────────────────────────────────────────────
PIXEL_SIZE_METERS = 0.30
OUTPUT_SIZE = 32
RENDER_SIZE = int(ceil(OUTPUT_SIZE * sqrt(2))) + 2  # ~48, enough margin for any rotation
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
    Azimuth is handled by rotating the car before render."""
    camera_obj = bpy.data.objects['-Camera']
    camera_data = camera_obj.data

    camera_data.type = 'ORTHO'
    camera_data.ortho_scale = PIXEL_SIZE_METERS * RENDER_SIZE

    ona_rad = ona_deg * pi / 180

    x = CAMERA_DIST * sin(ona_rad)
    y = 0
    z = CAMERA_DIST * cos(ona_rad)

    camera_obj.location = (x, y, z)
    
    # Fixed rotation: tilt by ONA from +X, always same orientation
    camera_obj.rotation_euler = (ona_rad, 0, pi / 2)

# ── MAIN RENDERING ──────────────────────────────────────────────────
def render_session(job):
    vehicles = job['vehicles']

    # Open base scene
    bpy.context.user_preferences.filepaths.use_relative_paths = False
    assert op.exists(SCENE_PATH), "Scene not found: %s" % SCENE_PATH
    bpy.ops.wm.open_mainfile(filepath=SCENE_PATH)

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

    # Import vehicle with fixed random orientation
    car_azimuth = uniform(low=0, high=360)
    # file_id = vehicles[0]['file_id']
    file_id = '%s_s%03d' % (vehicles[0]['file_id'], job['job_id'])

    car_names = []
    for i, vehicle in enumerate(vehicles):
        blend_path = get_blend_path(vehicle['file_id'])
        car_name = 'car-%d' % i
        car_names.append(car_name)
        import_blend_car(blend_path, vehicle['model_id'], car_name)
        obj = bpy.data.objects[car_name]
        obj.location.x += vehicle['x']
        obj.location.y += vehicle['y']
        obj.rotation_euler[2] += car_azimuth * pi / 180

    # Hide building
    bpy.data.objects['-Building'].hide_render = True

    # Materials
    for m in bpy.data.materials:
        m.use_transparent_shadows = True
        m.ambient = 0.0

    # Ground setup
    ground_obj = bpy.data.objects['-Ground']
    ground_obj.dimensions.x = 12
    ground_obj.dimensions.y = 12
    for mat in ground_obj.data.materials:
        if mat:
            mat.diffuse_intensity = 0.5

    # Road texture — fixed for the entire session
    road_textures = glob(op.join(ROAD_TEXTURE_DIR, '*.*'))
    if road_textures:
        bpy.data.images['ground'].filepath = choice(road_textures)

    # Rotate cars + ground for capture azimuth; post_process_renders() rotates
    # the raster back so the orthorectified background stays fixed.
    # Do not rotate -Sun here; set_weather() already controls sun angle.
    scene_objects = car_names + ['-Ground']

    # Render each snapshot
    for i in range(job['num_per_session']):
        # Random satellite parameters
        sat_azimuth = uniform(low=0, high=360)
        ona = uniform(low=0, high=20)
        sun_azimuth = uniform(low=0, high=360)
        sun_altitude = uniform(low=SUN_ALTITUDE_MIN, high=SUN_ALTITUDE_MAX)
        weather = choice(['Sunny', 'Cloudy', 'Sunny', 'Sunny'])

        # Camera fixed direction, only ONA changes
        setup_camera(ona)

        # Set weather and sun angle
        set_weather({'weather': weather, 'sun_altitude': sun_altitude, 'sun_azimuth': sun_azimuth})
        world = bpy.data.worlds.get('World')
        sun = bpy.data.objects.get('-Sun')

        logging.info("WORLD horizon=%s ambient=%s exposure=%s",
            tuple(world.horizon_color) if world else None,
            tuple(world.ambient_color) if world else None,
            getattr(world, 'exposure', None) if world else None)

        if sun:
            logging.info("SUN type=%s energy=%s rot=%s",
                    sun.data.type,
                    getattr(sun.data, 'energy', None),
                    tuple(round(v, 3) for v in sun.rotation_euler))

        for m in bpy.data.materials:
            name = m.name.lower()
            if any(x in name for x in ['glass', 'window', 'windshield', 'windscreen']):
                logging.info("GLASS mat=%s diffuse=%s diff_intensity=%s ambient=%s spec=%s alpha=%s",
                            m.name,
                            tuple(m.diffuse_color),
                            getattr(m, 'diffuse_intensity', None),
                            getattr(m, 'ambient', None),
                            getattr(m, 'specular_intensity', None),
                            getattr(m, 'alpha', None))

        # Rotate capture frame before orthorectification
        sat_az_rad = sat_azimuth * pi / 180
        for name in scene_objects:
            obj = bpy.data.objects[name]
            obj.rotation_euler[2] -= sat_az_rad

        bpy.context.scene.update()

        # Render
        raw_path = op.join(WORK_DIR, '%s_%03d_raw.png' % (file_id, i))
        bpy.data.scenes['Scene'].render.filepath = raw_path

        logging.info("Rendering %s_%03d  ONA=%.1f  sat_az=%.1f  car_az=%.1f" % (
            file_id, i, ona, sat_azimuth, car_azimuth))
        try:
            bpy.ops.render.render(write_still=True)
        except Exception as e:
            logging.error("Render error: %s" % str(e))

        # Undo scene rotation
        for name in scene_objects:
            obj = bpy.data.objects[name]
            obj.rotation_euler[2] += sat_az_rad

        # Write metadata
        out_info = {
            'model_id': vehicles[0]['model_id'],
            'file_id': file_id,
            'color': vehicles[0].get('color', 'unknown'),
            'car_azimuth': float(car_azimuth),
            'sat_azimuth': float(sat_azimuth),
            'capture_azimuth_prior_to_orthorect': float(sat_azimuth),
            'off_nadir_angle': float(ona),
            'pixel_size_m': PIXEL_SIZE_METERS,
            'weather': weather,
            'sun_azimuth': float(sun_azimuth),
            'sun_altitude': float(sun_altitude),
        }
        with open(op.join(WORK_DIR, '%s_%03d.json' % (file_id, i)), 'w') as f:
            json.dump(out_info, f, indent=2)

        logging.info("Snapshot %d done" % i)

# ── ENTRY POINT ─────────────────────────────────────────────────────
WORK_DIR = os.getenv('WORK_DIR_OVERRIDE')
if not WORK_DIR:
    raise Exception("Set WORK_DIR_OVERRIDE environment variable")

job_path = op.join(WORK_DIR, os.getenv('JOB_FILENAME', JOB_INFO_NAME))
assert op.exists(job_path), "Job file not found: %s" % job_path

job = json.load(open(job_path))
logging.basicConfig(level=job.get('logging', 20), stream=sys.stderr,
                    format='%(levelname)s:overhead: %(message)s')

render_session(job)