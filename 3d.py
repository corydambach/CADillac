import trimesh
import sys

def render_car_model(path_to_obj: str):
    """
    Load and render a 3D car model (OBJ format) from the 3DRealCar dataset.
    """
    # Load the model (with textures and materials if available)
    print(f"Loading model: {path_to_obj}")
    mesh = trimesh.load_mesh(path_to_obj, force='mesh')

    # Check if multiple meshes exist (some .obj files contain groups)
    if isinstance(mesh, list):
        mesh = trimesh.util.concatenate(mesh)

    # Print some basic info
    print(f"Vertices: {len(mesh.vertices)} | Faces: {len(mesh.faces)}")

    # Simple viewer
    scene = mesh.scene()
    scene.show()  # Opens an interactive pyglet window

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python render_car.py path/to/texture_output.obj")
        sys.exit(1)

    model_path = sys.argv[1]
    render_car_model(model_path)
