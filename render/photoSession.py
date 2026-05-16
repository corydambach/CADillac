# Copyright 2019 Evgeny Toropov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import bpy
import sys, os, os.path as op
import json
import logging
from math import cos, sin, pi, sqrt, ceil
import numpy as np
from glob import glob
from random import choice
from numpy.random import normal, uniform
from mathutils import Color, Euler
from math import cos, sin, pi, sqrt, ceil, tan

sys.path.insert(0, op.dirname(op.dirname(os.path.realpath(__file__)))) # = '..'
from render.common import *
from cads.collectionUtilities import getBlendPath
from render.renderUtil import atcadillac

import os
emergency_log = r'D:\\EMERGENCY_LOG.txt'
with open(emergency_log, 'w') as f: 
    f.write("photoSession.py IS RUNNING!\n")

COLLECTIONS_DIR  = atcadillac('CAD')
ROAD_TEXTURE_DIR = atcadillac('resources/textures/road')
BLDG_TEXTURE_DIR = atcadillac('resources/textures/buildings')
WORK_PATCHES_DIR = r'D:\tmp\blender\current-patch'
JOB_INFO_NAME    = 'job_info.json'
OUT_INFO_NAME    = 'out_info.json'

WORK_DIR = '%s-%d' % (WORK_PATCHES_DIR, os.getppid())

RENDER_WIDTH  = 224
RENDER_HEIGHT = 224
PIXEL_SIZE_METERS = 0.30  # Each pixel = 0.3 meters in Blender units

# sampling weather and camera position
SUN_ALTITUDE_MIN  = 20
SUN_ALTITUDE_MAX  = 70

def calculate_camera_height(pixel_size_meters, render_width):
    """
    Calculate the required camera height for orthographic-like projection.
    For satellite view, we want the FOV to cover a specific ground area.
    
    Args:
        pixel_size_meters: Size in meters that each pixel should represent
        render_width: Width of render in pixels
    
    Returns:
        camera_height: Height in meters for the camera
    """
    # Total ground coverage we want (in meters)
    ground_width = pixel_size_meters * render_width
    
    # For Blender's default camera (50mm lens equivalent):
    # We need to calculate height based on FOV and desired ground coverage
    # Using perspective projection: width = 2 * height * tan(fov/2)
    # Blender's default sensor width is 32mm, focal length is 50mm
    # This gives a horizontal FOV of about 39.6 degrees
    
    camera = bpy.data.objects['-Camera'].data
    fov_horizontal = camera.angle  # in radians
    
    # Calculate required height
    height = (ground_width / 2) / tan(fov_horizontal / 2)
    
    return height

def choose_params(azimuth_low, azimuth_high, pitch_low, pitch_high):
    ''' Pick some random parameters, adjust lighting, and finally render a frame. '''

    # pick random weather
    params = {}
    params['sun_azimuth']  = uniform(low=0, high=360)
    params['sun_altitude'] = uniform(low=SUN_ALTITUDE_MIN, high=SUN_ALTITUDE_MAX)
    params['weather'] = choice(['Rainy', 'Cloudy', 'Sunny', 'Wet'])
    set_weather (params)

    # For satellite view: camera looks straight down (altitude = 90)
    # but we can add slight variations if needed
    params['azimuth'] = uniform(low=azimuth_low, high=azimuth_high)  # rotation around Z-axis
    params['altitude'] = 90  # Always look straight down for satellite view
    # Optional: add small tilt for more realistic satellite imagery
    # params['altitude'] = uniform(low=85, high=90)  
    
    logging.info('prepare_photo: azimuth (deg): %.1f, altitude: %.1f (deg)' %
        (params['azimuth'], params['altitude']))
   
    # assign a random texture from the directory
    road_texture_path = choice(glob(op.join(ROAD_TEXTURE_DIR, '*.png')))
    logging.info('road_texture_path: %s' % road_texture_path)
    params['road_texture_path'] = road_texture_path
    # pick a random road width
    params['road_width'] = normal(15, 5)

    # assign a random texture from the directory
    buidling_texture_path = choice(glob(op.join(BLDG_TEXTURE_DIR, '*.png')))
    logging.info('buidling_texture_path: %s' % buidling_texture_path)
    params['buidling_texture_path'] = buidling_texture_path
    # pick a random height dim
    params['building.dimensions.z'] = normal(20, 5)
    # move randomly along a X and Z axes
    params['building.location.x'] = normal(0, 5)
    params['building.location.z'] = uniform(-5, 0)

    return params

def diagnose_camera():
    """Print all camera settings to understand what's going on"""
    camera_obj = bpy.data.objects['-Camera']
    camera_data = camera_obj.data
    
    logging.info('='*50)
    logging.info('CAMERA DIAGNOSTICS')
    logging.info('='*50)
    logging.info('Object name: %s' % camera_obj.name)
    logging.info('Position: %s' % camera_obj.location)
    logging.info('Rotation: %s' % camera_obj.rotation_euler)
    logging.info('Scale: %s' % camera_obj.scale)
    logging.info('Parent: %s' % camera_obj.parent)
    logging.info('Constraints: %d' % len(camera_obj.constraints))
    for c in camera_obj.constraints:
        logging.info('  - %s: %s' % (c.name, c.type))
    logging.info('-'*50)
    logging.info('Camera type: %s' % camera_data.type)
    logging.info('Focal length: %.1f mm' % camera_data.lens)
    logging.info('Sensor width: %.1f mm' % camera_data.sensor_width)
    if camera_data.type == 'ORTHO':
        logging.info('Ortho scale: %.2f' % camera_data.ortho_scale)
    logging.info('Clip start: %.2f' % camera_data.clip_start)
    logging.info('Clip end: %.2f' % camera_data.clip_end)
    logging.info('='*50)

def make_snapshot(car_sz, render_dir, car_names, params):
    '''Set up the weather, and render vehicles into files
    Args:
      render_dir:  path to directory where to put all rendered images
      car_names:   names of car objects in the scene
      params:      dictionary with frame information
    Returns:
      nothing
    '''
    logging.info('make_snapshot: started')    

    # Calculate camera height based on desired pixel size
    camera_height = calculate_camera_height(PIXEL_SIZE_METERS, RENDER_WIDTH)
    
    # For satellite view: position camera directly above the scene
    azimuth_rad = params['azimuth'] * pi / 180
    
    # Camera position: high above, centered on scene
    # Optional: add small x,y offset based on azimuth for slight angle
    x = 0  # Centered
    y = 0  # Centered
    z = camera_height
    logging.info( "Camera Height: " + str( z ) )
    
    camera = bpy.data.objects['-Camera']    
    camera_data = camera.data
    camera_data.type = 'PERSP'
    camera.location = (x, y, z)
    
    # Point camera straight down (90 degree pitch)
    # Rotation in Blender: (pitch, roll, yaw) in radians
    # For looking straight down: pitch=0 (horizontal forward), then rotate 90° around X
    camera.rotation_euler = (pi/2, 0, azimuth_rad)  # Look down, with rotation around Z
    diagnose_camera()  # See what's really going on
    
    logging.info('Camera position: x=%.2f, y=%.2f, z=%.2f (height=%.2f meters)' % 
                 (x, y, z, camera_height))
    logging.info('Ground coverage: %.2f x %.2f meters' % 
                 (PIXEL_SIZE_METERS * RENDER_WIDTH, PIXEL_SIZE_METERS * RENDER_HEIGHT))

    # set up lighting - sun should come from above at an angle
    sun_azimuth_rad = params['sun_azimuth'] * pi / 180
    sun_altitude_rad = params['sun_altitude'] * pi / 180
    
    # Position sun at distance, using spherical coordinates
    sun_dist = 100
    sun_x = sun_dist * cos(sun_altitude_rad) * cos(sun_azimuth_rad)
    sun_y = sun_dist * cos(sun_altitude_rad) * sin(sun_azimuth_rad)
    sun_z = sun_dist * sin(sun_altitude_rad)
    
    bpy.data.objects['-Sky-sunset'].location = (sun_x, sun_y, sun_z)
    bpy.data.objects['-Sky-sunset'].rotation_euler = (
        pi/2 - sun_altitude_rad,  # Tilt based on altitude
        0, 
        sun_azimuth_rad - pi/2
    )

    # set up road
    bpy.data.images['ground'].filepath = params['road_texture_path']
    bpy.data.objects['-Ground'].dimensions.x = params['road_width']

    # set up building
    bpy.data.images['building'].filepath = params['buidling_texture_path']
    bpy.data.objects['-Building'].dimensions.z = params['building.dimensions.z']
    bpy.data.objects['-Building'].location.x = params['building.location.x']
    bpy.data.objects['-Building'].location.z = params['building.location.z']
    bpy.data.objects['-Building'].location.y = params['road_width'] / 2
    bpy.data.objects['-Building'].hide_render = True  # Won't appear in renders

    # nodes to change output paths
    bpy.context.scene.node_tree.nodes['render'].base_path = atcadillac(render_dir)
    bpy.context.scene.node_tree.nodes['depth-all'].base_path = atcadillac(render_dir)
    bpy.context.scene.node_tree.nodes['depth-car'].base_path = atcadillac(render_dir)
    bpy.context.scene.node_tree.nodes['depth-road'].base_path = atcadillac(render_dir)
    bpy.context.scene.node_tree.nodes['depth-building'].base_path = atcadillac(render_dir)

    # make all cars receive shadows
    logging.info('materials: %s' % len(bpy.data.materials))
    for m in bpy.data.materials:
        m.use_transparent_shadows = True

    # add cars to Cars and Depth-all layers and the main car to Depth-car layer
    for car_name in car_names:
        for layer_id in range(5):
            bpy.data.objects[car_name].layers[layer_id] = False
    for car_name in car_names:
        bpy.data.objects[car_name].layers[0] = True
        bpy.data.objects[car_name].layers[1] = True
    bpy.data.objects[car_names[0]].layers[2] = True

    # Render scene
    try:
        bpy.ops.render.render(write_still=True)
    except:
        logging.error("Error!")
        pass

    # change the names of output png files
    for layer_name in ['render', 'depth-all', 'depth-car', 'depth-road', 'depth-building']:
        os.rename(atcadillac(op.join(render_dir, '%s0001' % layer_name)), 
                  atcadillac(op.join(render_dir, '%s.png' % layer_name)))

    ### aftermath
    
    for car_name in car_names:
        show_car(car_name)

    logging.info('make_snapshot: successfully finished a frame')
    
def photo_session(job):
    '''Take pictures of a scene from different random angles, 
      given some cars placed and fixed in the scene.
    '''
    num_per_session = job['num_per_session']
    vehicles        = job['vehicles']

    azimuth_low = job['azimuth_low']
    azimuth_high = job['azimuth_high']
    pitch_low = job['pitch_low']
    pitch_high = job['pitch_high']

    # open the blender file
    bpy.context.user_preferences.filepaths.use_relative_paths = False
    scene_path = op.join(op.dirname(os.path.realpath(__file__)), 'resources/photo-session.blend')
    logging.info('Looking for photo-session.blend at %s' % scene_path)
    bpy.ops.wm.open_mainfile(filepath=scene_path)

    render_dir = op.join(WORK_DIR, '%06d' % 0)
    if not op.exists(atcadillac(render_dir)):
        os.makedirs(atcadillac(render_dir))
    if job['save_blender']:
        bpy.ops.wm.save_as_mainfile(filepath=atcadillac(op.join(render_dir, 'out1.blend')))

    # Use satellite view resolution
    if 'render_width' not in job: job['render_width'] = RENDER_WIDTH
    if 'render_height' not in job: job['render_height'] = RENDER_HEIGHT
    bpy.context.scene.render.resolution_x = job['render_width']
    bpy.context.scene.render.resolution_y = job['render_height']

    car_names = []
    for i, vehicle in enumerate(vehicles):
        blend_path = getBlendPath(vehicle['id'])

        assert op.exists(blend_path), 'blend path does not exist %s' % blend_path

        car_name = 'car-%d' % i
        car_names.append(car_name)
        import_blend_car(blend_path, vehicle['model_id'], car_name)
        position_car(car_name, vehicles[i]['x'], vehicles[i]['y'], vehicles[i]['azimuth'])

    # take snapshots from different angles
    dims = vehicles[0]['dims']  # dict with 'x', 'y', 'z' in meters
    car_sz = sqrt(dims['x']**2 + dims['y']**2 + dims['z']**2)
    
    for i in range(num_per_session):
        render_dir = op.join(WORK_DIR, '%06d' % i)
        # create render dir
        if not op.exists(atcadillac(render_dir)):
            os.makedirs(atcadillac(render_dir))

        use_90turn = job['use_90turn'] if 'use_90turn' in job else False
        logging.info('use_90turn %s' % str(use_90turn))
        
        if use_90turn and i % 2 == 1:
            logging.info('using use_90turn')
            params['azimuth'] = (params['azimuth'] + 90) % 360
        else:
            logging.info('PREPARED: using azimuth: %.1f - %.1f, pitch: %.1f - %.1f' % 
                (azimuth_low, azimuth_high, pitch_low, pitch_high))
            params = choose_params(azimuth_low, azimuth_high, pitch_low, pitch_high)

        if job['save_blender']:
            bpy.ops.wm.save_as_mainfile(filepath=atcadillac(op.join(render_dir, 'out2.blend')))

        make_snapshot(car_sz, render_dir, car_names, params)

        ### write down some labelling info
        out_path = atcadillac(op.join(render_dir, OUT_INFO_NAME))
        with open(out_path, 'w') as f:
            f.write(json.dumps({
                'azimuth':      params['azimuth'],  # For satellite, this is rotation
                'altitude':     params['altitude'],  # Should be 90
                'model_id':     vehicles[0]['model_id'],
                'color':        vehicles[0]['color'],
                'pixel_size_m': PIXEL_SIZE_METERS,
                'camera_height_m': calculate_camera_height(PIXEL_SIZE_METERS, job['render_width'])
            }, indent=4))
            
        print("$$$")
        print(op.join(WORK_DIR, JOB_INFO_NAME))

with open(emergency_log, 'w') as f: 
    f.write("photoSession.py IS RUNNING!\n")


with open(emergency_log, 'w') as f: 
    f.write("photoSession.py IS RUNNING!\n")

job = json.load(open( op.join(WORK_DIR, JOB_INFO_NAME) ))
logging.basicConfig(level=job['logging'], stream=sys.stderr, 
    format='%(levelname)s:photosession: %(message)s')

photo_session (job)

