"""
Batch classification rendering script for Blender 2.79.
Reads a jobs.json file and renders top/side/front/rear orthographic views
of each vehicle at 32x32 pixels.

The base scene is reloaded from scratch for every vehicle to avoid cleanup issues.
"""

import bpy
import sys
import os
import os.path as op
import json
import logging
import traceback
from math import pi

sys.path.insert(0, op.dirname(op.dirname(os.path.realpath(__file__))))
from render.common import *
from render.renderUtil import atcadillac

# ── PATHS ───────────────────────────────────────────────────────────
if not os.getenv('CADILLAC_DATA_PATH'):
    raise Exception('Set CADILLAC_DATA_PATH environment variable')

SCENE_PATH = op.join(op.dirname(os.path.realpath(__file__)), 'resources', 'photo-session.blend')

# ── RENDERING PARAMS ────────────────────────────────────────────────
PIXEL_SIZE_METERS = 0.30
OUTPUT_SIZE = 256
CAMERA_DIST = 50.0

# View definitions: (rotation_x, rotation_y, rotation_z) for the camera
# Camera looks down -Z in its local space after rotation.
# These place the camera at the correct position and orient it toward the origin.
VIEW_CONFIG = {
    'top':   {'location': (0, 0, CAMERA_DIST),            'rotation': (0, 0, 0)},
    'front': {'location': (0, -CAMERA_DIST, 0),           'rotation': (pi / 2, 0, 0)},
    'rear':  {'location': (0, CAMERA_DIST, 0),            'rotation': (pi / 2, 0, pi)},
    'side':  {'location': (CAMERA_DIST, 0, 0),            'rotation': (pi / 2, 0, pi / 2)},
}


def get_blend_path(file_id):
    path = atcadillac(op.join('blend', '%s.blend' % file_id))
    assert op.exists(path), "Blend not found: %s" % path
    return path


def import_blend_car(blend_path, model_id, car_name='car'):
    with bpy.data.libraries.load(blend_path, link=False) as (data_src, data_dst):
        data_dst.objects = [model_id]
    obj = data_dst.objects[0]
    assert obj is not None, "Failed to load object %s from %s" % (model_id, blend_path)
    bpy.context.scene.objects.link(obj)
    obj.name = car_name
    return obj


def setup_camera(view_name):
    """Configure orthographic camera for the given view."""
    cfg = VIEW_CONFIG[view_name]
    camera_obj = bpy.data.objects['-Camera']
    camera_data = camera_obj.data

    camera_data.type = 'ORTHO'
    camera_data.ortho_scale = PIXEL_SIZE_METERS * OUTPUT_SIZE

    camera_obj.location = cfg['location']
    camera_obj.rotation_euler = cfg['rotation']


def setup_scene():
    """Common scene setup after loading the base .blend file."""
    bpy.context.user_preferences.filepaths.use_relative_paths = False
    assert op.exists(SCENE_PATH), "Scene not found: %s" % SCENE_PATH
    bpy.ops.wm.open_mainfile(filepath=SCENE_PATH)

    bpy.ops.object.select_all(action='DESELECT')

    bpy.context.scene.render.resolution_x = OUTPUT_SIZE
    bpy.context.scene.render.resolution_y = OUTPUT_SIZE
    bpy.context.scene.render.resolution_percentage = 100

    # Disable mist
    bpy.data.worlds['World'].mist_settings.use_mist = False

    # Mute compositor file output nodes
    if bpy.context.scene.node_tree:
        for node in bpy.context.scene.node_tree.nodes:
            if node.type == 'OUTPUT_FILE':
                node.mute = True

    # Hide building
    if '-Building' in bpy.data.objects:
        bpy.data.objects['-Building'].hide_render = True

    # Neutral lighting: bright, even, overhead sun
    set_weather({'weather': 'Sunny', 'sun_altitude': 80, 'sun_azimuth': 180})

    # Materials
    for m in bpy.data.materials:
        m.use_transparent_shadows = True
        m.ambient = 0.5

    # Minimal ground
    if '-Ground' in bpy.data.objects:
        ground_obj = bpy.data.objects['-Ground']
        ground_obj.hide_render = True


def render_vehicle(job, work_dir):
    """Reload scene, import vehicle, render all views."""
    model_id = job['model_id']
    file_id = job['file_id']
    views = job['views']

    logging.info("Processing %s (file_id=%s)", model_id, file_id)

    # Reload base scene from scratch
    setup_scene()

    # Import vehicle
    blend_path = get_blend_path(file_id)
    import_blend_car(blend_path, model_id)

    bpy.context.scene.update()

    # Render each view
    for view_name in views:
        setup_camera(view_name)
        bpy.context.scene.update()

        out_path = op.join(work_dir, '%s_%s.png' % (file_id, view_name))
        bpy.data.scenes['Scene'].render.filepath = out_path

        logging.info("  Rendering %s_%s", file_id, view_name)
        try:
            bpy.ops.render.render(write_still=True)
        except Exception as e:
            logging.error("  Render error for %s_%s: %s", file_id, view_name, str(e))

    logging.info("  Done: %s (%d views)", model_id, len(views))


# ── ENTRY POINT ─────────────────────────────────────────────────────
WORK_DIR = os.getenv('WORK_DIR_OVERRIDE')
if not WORK_DIR:
    raise Exception("Set WORK_DIR_OVERRIDE environment variable")

jobs_path = op.join(WORK_DIR, 'jobs.json')
assert op.exists(jobs_path), "Jobs file not found: %s" % jobs_path

with open(jobs_path) as f:
    jobs = json.load(f)

logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                    format='%(levelname)s:classify: %(message)s')

logging.info("Loaded %d jobs from %s", len(jobs), jobs_path)

failed = 0
for i, job in enumerate(jobs):
    logging.info("[%d/%d] %s", i + 1, len(jobs), job['model_id'])
    try:
        render_vehicle(job, WORK_DIR)
    except Exception as e:
        logging.error("FAILED %s: %s", job['model_id'], str(e))
        logging.error(traceback.format_exc())
        failed += 1

logging.info("Complete. %d succeeded, %d failed.", len(jobs) - failed, failed)