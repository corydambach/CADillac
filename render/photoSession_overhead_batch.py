"""
Batch overhead rendering script for Blender 2.79 — optimised.

Key changes vs. original:
 1. Scene opened ONCE; cleaned/reset between jobs (not reopened).
 2. Mesh-level .data.transform() replaced with object-level rotation
    so geometry stays pristine and reusable.
 3. Car blend files cached — each .blend is loaded once, then duplicated.
 4. GPU compute enabled automatically when available.
 5. Tile size tuned for GPU vs CPU.
 6. Road textures pre-globbed once.
 7. Constant render settings applied once, not per-job.
 8. Optional multi-process parallelism via BATCH_WORKERS.
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
from mathutils import Matrix
from collections import OrderedDict

sys.path.insert(0, op.dirname(op.dirname(os.path.realpath(__file__))))
from render.common import *
from render.renderUtil import atcadillac

import os
_devnull = open(os.devnull, 'w')
os.dup2(_devnull.fileno(), 1)

# ── PATHS ───────────────────────────────────────────────────────────
if not os.getenv('CADILLAC_DATA_PATH'):
    raise Exception('Set CADILLAC_DATA_PATH environment variable')

SCENE_PATH       = op.join(op.dirname(os.path.realpath(__file__)), 'resources', 'photo-session.blend')
ROAD_TEXTURE_DIR = atcadillac('resources/textures/road')

# ── RENDERING PARAMS ────────────────────────────────────────────────
PIXEL_SIZE_METERS = 0.30
OUTPUT_SIZE       = 32
RENDER_SIZE       = int(ceil(OUTPUT_SIZE * sqrt(2))) + 2
CAMERA_DIST       = 50.0

ROLE_SHORT = {
    'anchor':               'a',
    'positive':             'p',
    'negative_orientation': 'nr',
    'negative_identity':    'ni',
    'negative_translation': 'nt',
    'negative_empty':       'ne',
}

# ── CACHES (populated once) ─────────────────────────────────────────
_car_cache     = OrderedDict()
_CAR_CACHE_MAX = int(os.getenv('CAR_CACHE_MAX', 5))
_road_textures = None  # list of texture paths


def _get_road_textures():
    global _road_textures
    if _road_textures is None:
        _road_textures = glob(op.join(ROAD_TEXTURE_DIR, '*.*'))
    return _road_textures


def get_blend_path(file_id):
    path = atcadillac(op.join('blend', '%s.blend' % file_id))
    assert op.exists(path), "Blend not found: %s" % path
    return path


def _evict_template(key):
    """Remove a cached template object and its data from the scene."""
    name = _car_cache.pop(key)
    obj = bpy.data.objects.get(name)
    if obj is None:
        return
    bpy.context.scene.objects.unlink(obj)
    mesh = obj.data
    bpy.data.objects.remove(obj)
    if mesh and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def import_or_get_cached(file_id, model_id):
    key = (file_id, model_id)

    if key in _car_cache:
        _car_cache.move_to_end(key)          # mark as recently used
    else:
        # Evict least-recently-used if at capacity
        while len(_car_cache) >= _CAR_CACHE_MAX:
            oldest_key = next(iter(_car_cache))
            _evict_template(oldest_key)

        blend_path = get_blend_path(file_id)
        with bpy.data.libraries.load(blend_path, link=False) as (data_src, data_dst):
            data_dst.objects = [model_id]
        template = data_dst.objects[0]
        assert template is not None, "Failed to load %s from %s" % (model_id, blend_path)
        bpy.context.scene.objects.link(template)
        template.hide = True
        template.hide_render = True
        _car_cache[key] = template.name

    # Duplicate from template
    src = bpy.data.objects[_car_cache[key]]
    dup = src.copy()
    dup.data = src.data.copy()
    dup.hide = False
    dup.hide_render = False
    bpy.context.scene.objects.link(dup)
    return dup

def setup_camera(ona_deg):
    cam_obj = bpy.data.objects['-Camera']
    cam_obj.data.type = 'ORTHO'
    cam_obj.data.ortho_scale = PIXEL_SIZE_METERS * RENDER_SIZE

    ona_rad = ona_deg * pi / 180
    cam_obj.location = (
        CAMERA_DIST * sin(ona_rad),
        0,
        CAMERA_DIST * cos(ona_rad),
    )
    cam_obj.rotation_euler = (ona_rad, 0, pi / 2)


# ── PER-JOB CLEANUP ────────────────────────────────────────────────

def _cleanup_job_objects(spawned_names):
    """Remove objects we spawned for the last job."""
    for name in spawned_names:
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        bpy.context.scene.objects.unlink(obj)
        mesh = obj.data
        bpy.data.objects.remove(obj)
        if mesh and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _reset_scene_objects():
    """Reset rotation on persistent objects that get rotated per-job."""
    for name in ('-Ground', '-Sun'):
        obj = bpy.data.objects.get(name)
        if obj:
            obj.rotation_euler = (0, 0, 0)
    ground = bpy.data.objects.get('-Ground')
    if ground:
        ground.dimensions.x = 12
        ground.dimensions.y = 12


# ── GPU / RENDER SETTINGS (once) ───────────────────────────────────

def _configure_render_device():
    """Enable GPU Compute if a CUDA/OpenCL device is available."""
    prefs = bpy.context.user_preferences
    try:
        cprefs = prefs.addons['cycles'].preferences
        # Try CUDA first, fall back to OpenCL
        for ctype in ('CUDA', 'OPENCL'):
            cprefs.compute_device_type = ctype
            cprefs.get_devices()  # refresh list
            gpus = [d for d in cprefs.devices if d.type != 'CPU']
            if gpus:
                for d in cprefs.devices:
                    d.use = (d.type != 'CPU')
                logging.info("GPU enabled: %s (%s)", gpus[0].name, ctype)
                return 'GPU'
    except Exception:
        pass
    logging.info("No GPU found — using CPU")
    return 'CPU'


def _apply_constant_settings(device_type):
    scn = bpy.context.scene
    rd = scn.render

    rd.resolution_x = RENDER_SIZE
    rd.resolution_y = RENDER_SIZE
    rd.resolution_percentage = 100
    bpy.data.worlds['World'].mist_settings.use_mist = False

    # Tile size: large for GPU, small for CPU
    if device_type == 'GPU':
        rd.tile_x = min(RENDER_SIZE, 256)
        rd.tile_y = min(RENDER_SIZE, 256)
    else:
        rd.tile_x = 16
        rd.tile_y = 16

    if scn.render.engine == 'CYCLES':
        scn.cycles.device = 'GPU' if device_type == 'GPU' else 'CPU'

    # Mute file-output nodes once
    if scn.node_tree:
        for node in scn.node_tree.nodes:
            if node.type == 'OUTPUT_FILE':
                node.mute = True

    bpy.data.objects['-Building'].hide_render = True

    # Material defaults (these survive across jobs)
    for m in bpy.data.materials:
        m.use_transparent_shadows = True
        m.ambient = 0.5

    ground = bpy.data.objects['-Ground']
    ground.dimensions.x = 12
    ground.dimensions.y = 12
    for mat in ground.data.materials:
        if mat:
            mat.diffuse_intensity = 0.5


# ── RENDER ONE JOB ──────────────────────────────────────────────────

def render_one_job(job, work_dir):
    vehicles   = job['vehicles']
    car_az_rad = job['car_azimuth'] * pi / 180
    sat_az_rad = job['sat_azimuth'] * pi / 180
    ona        = job['off_nadir_angle']

    group_id   = job.get('group_id', 0)
    role       = job.get('role', 'unknown')
    model_id   = vehicles[0]['model_id'] if vehicles else 'none'
    sample_idx = job.get('sample_idx', 0)
    job_id     = job.get('job_id', 0)
    out_prefix = 'g%d-%s-%d' % (group_id, ROLE_SHORT.get(role, role), sample_idx)
    spawned    = []

    # ── Import / duplicate vehicles ──
    for i, vehicle in enumerate(vehicles):
        car_name = 'car-%d' % i
        obj = import_or_get_cached(vehicle['file_id'], vehicle['model_id'])
        obj.name = car_name
        spawned.append(car_name)

        #change trying to fix rotation bug
        if obj.rotation_mode == 'QUATERNION':
            rot_mat = obj.rotation_quaternion.to_matrix().to_4x4()
        else:
            rot_mat = obj.rotation_euler.to_matrix().to_4x4()
        obj.data.transform(rot_mat)
        obj.rotation_euler = (0, 0, 0)
        obj.rotation_quaternion = (1, 0, 0, 0)
        #end of change

        obj.location.x = vehicle.get('x', 0.0)
        obj.location.y = vehicle.get('y', 0.0)
        obj.location.z = 0.0
        # Mesh-level rotation (same as original) — safe because each
        # duplicate has its own mesh copy via src.data.copy()
        obj.data.transform(Matrix.Rotation(car_az_rad, 4, 'Z'))

    # ── Road texture ──
    tex_path = job.get('road_texture')
    if not tex_path:
        roads = _get_road_textures()
        if roads:
            tex_path = choice(roads)
    if tex_path:
        img = bpy.data.images['ground']
        if img.filepath != tex_path:  # skip reload if same
            img.filepath = tex_path
            img.reload()

    # ── Camera & lighting ──
    setup_camera(ona)
    set_weather({
        'weather':      job['weather'],
        'sun_altitude': job['sun_altitude'],
        'sun_azimuth':  job['sun_azimuth'],
    })

    # ── Rotate everything by -sat_azimuth ──
    # Cars: mesh-level (safe — each has its own mesh copy)
    for name in spawned:
        obj = bpy.data.objects[name]
        if obj.type == 'MESH':
            obj.data.transform(Matrix.Rotation(-sat_az_rad, 4, 'Z'))
        else:
            obj.rotation_euler[2] -= sat_az_rad
    # Persistent objects: object-level (reset between jobs)
    ground = bpy.data.objects['-Ground']
    sun    = bpy.data.objects['-Sun']
    ground.rotation_euler = (0, 0, -sat_az_rad)
    sun.rotation_euler    = (sun.rotation_euler.x, sun.rotation_euler.y,
                             sun.rotation_euler.z - sat_az_rad)

    bpy.context.scene.update()

    # ── Render ──
    raw_path = op.join(work_dir, '%s_raw.png' % out_prefix)
    bpy.data.scenes['Scene'].render.filepath = raw_path

    logging.info("[job %d] Rendering %s  ONA=%.1f  sat_az=%.1f  car_az=%.1f",
                 job_id, out_prefix, ona, job['sat_azimuth'], job['car_azimuth'])
    try:
        bpy.ops.render.render(write_still=True)
    except Exception as e:
        logging.error("Render error: %s", str(e))

    # ── Metadata ──
    out_info = {
        'group_id':        group_id,
        'role':            role,
        'model_id':        model_id,
        'file_id':         out_prefix,
        'sat_azimuth':     float(job['sat_azimuth']),
        'off_nadir_angle': float(ona),
        'pixel_size_m':    PIXEL_SIZE_METERS,
        'weather':         job['weather'],
        'sun_azimuth':     float(job['sun_azimuth']),
        'sun_altitude':    float(job['sun_altitude']),
    }
    if vehicles:
        out_info.update({
            'color':       vehicles[0].get('color', 'unknown'),
            'car_azimuth': float(job['car_azimuth']),
            'car_x':       float(vehicles[0].get('x', 0.0)),
            'car_y':       float(vehicles[0].get('y', 0.0)),
        })
    with open(op.join(work_dir, '%s.json' % out_prefix), 'w') as f:
        json.dump(out_info, f, indent=2)

    # ── Cleanup spawned cars (keep templates) ──
    _cleanup_job_objects(spawned)
    _reset_scene_objects()


# ── BATCH RUNNER ────────────────────────────────────────────────────
def run_batch(jobs, work_dir):
    prefs = bpy.context.user_preferences
    prefs.filepaths.use_relative_paths = False
    assert op.exists(SCENE_PATH), "Scene not found: %s" % SCENE_PATH

    chunk_size = int(os.getenv('BATCH_CHUNK_SIZE', 15))
    n = len(jobs)
    succeeded = 0
    failed = 0

    for chunk_start in range(0, n, chunk_size):
        chunk = jobs[chunk_start:chunk_start + chunk_size]

        # Fresh scene — wipes orphans, undo, BVH cache, everything
        bpy.ops.wm.open_mainfile(filepath=SCENE_PATH)
        _car_cache.clear()

        device = _configure_render_device()
        _apply_constant_settings(device)

        for idx, job in enumerate(chunk):
            global_idx = chunk_start + idx
            sys.stderr.write( os.linesep )
            logging.info("[%d/%d] job_id=%s group=%s role=%s",
                         global_idx + 1, n,
                         job.get('job_id'), job.get('group_id'), job.get('role'))
            try:
                render_one_job(job, work_dir)
                succeeded += 1
            except Exception as e:
                logging.error("Job %s failed: %s", job.get('job_id'), str(e))
                logging.error(traceback.format_exc())
                failed += 1

        logging.info("Chunk done - reopening scene to reclaim memory")

    logging.info("=" * 60)
    logging.info("Batch complete: %d succeeded, %d failed out of %d",
                 succeeded, failed, n)

#entry point
WORK_DIR = os.getenv('WORK_DIR_OVERRIDE')
if not WORK_DIR:
    raise Exception("Set WORK_DIR_OVERRIDE environment variable")

jobs_filename = os.getenv('JOBS_FILENAME', 'jobs.json')
jobs_path = op.join(WORK_DIR, jobs_filename)
assert op.exists(jobs_path), "Jobs file not found: %s" % jobs_path

with open(jobs_path) as f:
    jobs = json.load(f)

log_level = jobs[0].get('logging', 20) if jobs else 20
logging.basicConfig(level=log_level, stream=sys.stderr, format='%(levelname)s %(message)s')
logging.info("Loaded %d jobs from %s", len(jobs), jobs_path)

try:
    run_batch(jobs, WORK_DIR)
except Exception as e:
    logging.error("FATAL: %s", str(e))
    logging.error(traceback.format_exc())
