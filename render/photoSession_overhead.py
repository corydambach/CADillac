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
OUTPUT_SIZE = 64
RENDER_SIZE = int(ceil(OUTPUT_SIZE * sqrt(2))) + 2  # ~48, enough margin for any rotation
CAMERA_DIST = 50.0

SUN_ALTITUDE_MIN = 20
SUN_ALTITUDE_MAX = 70


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


# ── WEATHER ─────────────────────────────────────────────────────────
def set_weather(params):
    weather = params.get('weather', 'Sunny')
    sun = bpy.data.objects['-Sun']

    if weather in ('Rainy', 'Wet'):
        sun.hide_render = True
        sun.hide = True
        mat = bpy.data.materials.get('Material-wet-asphalt')
    else:
        sun.hide_render = False
        sun.hide = False
        sun.data.energy = normal(3, 1.0)
        sun.data.color = (1.0, 0.9163, 0.6905)
        mat = bpy.data.materials.get('Material-dry-asphalt')

    if mat:
        ground = bpy.data.objects['-Ground']
        if len(ground.data.materials):
            ground.data.materials[0] = mat
        else:
            ground.data.materials.append(mat)


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


# ── IMAGE ROTATION & CROP ──────────────────────────────────────────
def rotate_and_crop(input_path, output_path, angle_deg, output_size):
    """Load rendered image, rotate by angle_deg, crop center to output_size.
    Uses nearest-neighbor rotation via numpy."""
    # Load image
    img = bpy.data.images.load(input_path)
    w, h = img.size
    pixels = np.array(img.pixels[:])  # flat RGBA array
    pixels = pixels.reshape((h, w, 4))
    pixels = np.flipud(pixels)  # Blender stores bottom-up

    # Rotation matrix
    angle_rad = angle_deg * pi / 180
    cos_a = cos(angle_rad)
    sin_a = sin(angle_rad)

    # Output centered on input center
    cx, cy = w / 2.0, h / 2.0
    out = np.zeros((output_size, output_size, 4), dtype=np.float32)
    offset = (w - output_size) / 2.0

    for oy in range(output_size):
        for ox in range(output_size):
            # Map output pixel to input pixel through inverse rotation
            dx = ox + offset - cx
            dy = oy + offset - cy
            sx = int(cos_a * dx + sin_a * dy + cx)
            sy = int(-sin_a * dx + cos_a * dy + cy)
            if 0 <= sx < w and 0 <= sy < h:
                out[oy, ox] = pixels[sy, sx]

    # Save via Blender
    out_img = bpy.data.images.new("rotated", output_size, output_size)
    out = np.flipud(out)  # back to Blender bottom-up
    out_img.pixels = out.flatten().tolist()
    out_img.filepath_raw = output_path
    out_img.file_format = 'PNG'
    out_img.save_render(output_path)

    # Cleanup
    bpy.data.images.remove(img)
    bpy.data.images.remove(out_img)


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
    file_id = vehicles[0]['file_id']

    car_names = []
    for i, vehicle in enumerate(vehicles):
        blend_path = get_blend_path(vehicle['file_id'])
        car_name = 'car-%d' % i
        car_names.append(car_name)
        import_blend_car(blend_path, vehicle['model_id'], car_name)
        bpy.ops.object.select_all(action='DESELECT')
        bpy.data.objects[car_name].select = True
        bpy.context.scene.objects.active = bpy.data.objects[car_name]
        bpy.ops.transform.translate(value=(vehicle['x'], vehicle['y'], 0))
        bpy.ops.transform.rotate(value=car_azimuth * pi / 180, axis=(0, 0, 1))

    # Hide building
    bpy.data.objects['-Building'].hide_render = True

    # Materials
    for m in bpy.data.materials:
        m.use_transparent_shadows = True

    # Ground setup
    ground_obj = bpy.data.objects['-Ground']
    ground_obj.dimensions.x = 100
    ground_obj.dimensions.y = 100
    for mat in ground_obj.data.materials:
        if mat:
            mat.diffuse_intensity = 0.3

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

        # Rotate car by -sat_azimuth so camera sees correct side
        sat_az_rad = sat_azimuth * pi / 180
        for car_name in car_names:
            obj = bpy.data.objects[car_name]
            bpy.ops.object.select_all(action='DESELECT')
            obj.select = True
            bpy.context.scene.objects.active = obj
            bpy.ops.transform.rotate(value=-sat_az_rad, axis=(0, 0, 1))

        # Set weather
        set_weather({'weather': weather, 'sun_altitude': sun_altitude, 'sun_azimuth': sun_azimuth})

        # Road texture
        road_textures = glob(op.join(ROAD_TEXTURE_DIR, '*.*'))
        if road_textures:
            bpy.data.images['ground'].filepath = choice(road_textures)

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

        # Undo car rotation
        for car_name in car_names:
            obj = bpy.data.objects[car_name]
            bpy.ops.object.select_all(action='DESELECT')
            obj.select = True
            bpy.context.scene.objects.active = obj
            bpy.ops.transform.rotate(value=sat_az_rad, axis=(0, 0, 1))

        # Write metadata
        out_info = {
            'model_id': vehicles[0]['model_id'],
            'file_id': file_id,
            'color': vehicles[0].get('color', 'unknown'),
            'car_azimuth': float(car_azimuth),
            'sat_azimuth': float(sat_azimuth),
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