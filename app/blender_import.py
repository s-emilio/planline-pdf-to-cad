from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bpy


def _arguments() -> tuple[Path, Path, Path, float, float, float, float, str, str]:
    separator = sys.argv.index("--")
    svg, source_png, blend, width, height, offset_x, offset_y, name, units = (
        sys.argv[separator + 1 :]
    )
    return (
        Path(svg),
        Path(source_png),
        Path(blend),
        float(width),
        float(height),
        float(offset_x),
        float(offset_y),
        name,
        units,
    )


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def _join_meshes(objects: list[bpy.types.Object]) -> bpy.types.Object:
    bpy.ops.object.select_all(action="DESELECT")
    for object_ in objects:
        object_.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    if len(objects) > 1:
        bpy.ops.object.join()
    return bpy.context.active_object


def _flatten_and_flip_mesh(object_: bpy.types.Object) -> None:
    object_.rotation_euler.x += math.radians(90)
    object_.rotation_euler.x += math.radians(180)
    bpy.context.view_layer.objects.active = object_
    object_.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    mesh = object_.data
    if mesh.polygons:
        for polygon in mesh.polygons:
            polygon.select = True
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.delete(type="ONLY_FACE")
        bpy.ops.object.mode_set(mode="OBJECT")

    connected = {index for edge in mesh.edges for index in edge.vertices}
    if len(connected) != len(mesh.vertices):
        remove = [vertex.index for vertex in mesh.vertices if vertex.index not in connected]
        if remove:
            bpy.context.view_layer.objects.active = object_
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="DESELECT")
            bpy.ops.object.mode_set(mode="OBJECT")
            for index in remove:
                mesh.vertices[index].select = True
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.delete(type="VERT")
            bpy.ops.object.mode_set(mode="OBJECT")

    for vertex in mesh.vertices:
        vertex.co.z = 0
    mesh.update()


def _source_plane(
    image_path: Path,
    width_m: float,
    height_m: float,
    offset_x_m: float,
    offset_y_m: float,
) -> bpy.types.Object:
    half_width = width_m / 2
    half_height = height_m / 2
    mesh = bpy.data.meshes.new("PDF_Source_Plane_Mesh")
    mesh.from_pydata(
        [
            (-half_width, -half_height, 0),
            (half_width, -half_height, 0),
            (half_width, half_height, 0),
            (-half_width, half_height, 0),
        ],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    plane = bpy.data.objects.new("PDF_Source_Plane", mesh)
    bpy.context.collection.objects.link(plane)

    uv_layer = mesh.uv_layers.new(name="UVMap")
    for loop, coordinate in zip(
        uv_layer.data,
        ((0, 0), (1, 0), (1, 1), (0, 1)),
    ):
        loop.uv = coordinate

    image = bpy.data.images.load(str(image_path), check_existing=False)
    image.name = "PDF_Source"
    image.pack()
    material = bpy.data.materials.new("PDF_Source_Material")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    image_node = nodes.new("ShaderNodeTexImage")
    image_node.image = image
    image_node.interpolation = "Linear"
    shader = nodes.get("Principled BSDF")
    shader.inputs["Roughness"].default_value = 1.0
    links.new(image_node.outputs["Color"], shader.inputs["Base Color"])
    mesh.materials.append(material)

    plane.location = (offset_x_m, -offset_y_m, -0.002)
    plane.rotation_euler.x = math.radians(180)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = plane
    plane.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    plane["planline_source_png"] = image_path.name
    plane["planline_image_packed"] = True
    plane["planline_offset_below_edges_m"] = 0.002
    plane["planline_alignment_offset_x_m"] = offset_x_m
    plane["planline_alignment_offset_y_m"] = -offset_y_m
    plane["planline_x_rotation_applied_degrees"] = 180
    return plane


def main() -> None:
    (
        svg_path,
        source_png,
        blend_path,
        width_m,
        height_m,
        plane_offset_x_m,
        plane_offset_y_m,
        drawing_name,
        source_units,
    ) = _arguments()
    _clear_scene()
    result = bpy.ops.wm.grease_pencil_import_svg(
        filepath=str(svg_path),
        resolution=8,
        scale=1.0,
        use_scene_unit=False,
    )
    if "FINISHED" not in result:
        raise RuntimeError("Blender could not import the SVG as Grease Pencil.")

    grease_pencil_objects = [
        object_
        for object_ in bpy.context.scene.objects
        if object_.type == "GREASEPENCIL"
    ]
    if not grease_pencil_objects:
        raise RuntimeError("The SVG import produced no Grease Pencil objects.")

    meshes: list[bpy.types.Object] = []
    for object_ in grease_pencil_objects:
        bpy.ops.object.select_all(action="DESELECT")
        object_.select_set(True)
        bpy.context.view_layer.objects.active = object_
        bpy.ops.object.convert(target="MESH")
        meshes.append(bpy.context.active_object)

    edge_mesh = _join_meshes(meshes)
    _flatten_and_flip_mesh(edge_mesh)
    edge_mesh.name = "Plan_Edges"
    edge_mesh.data.name = "Plan_Edges_Mesh"
    edge_mesh.display_type = "WIRE"
    edge_mesh["planline_source_svg"] = svg_path.name
    edge_mesh["planline_drawing_name"] = drawing_name
    edge_mesh["planline_source_units"] = source_units
    edge_mesh["planline_width_m"] = width_m
    edge_mesh["planline_height_m"] = height_m
    edge_mesh["planline_scale_verified"] = True
    edge_mesh["planline_x_rotation_applied_degrees"] = 180

    source_plane = _source_plane(
        source_png,
        width_m,
        height_m,
        plane_offset_x_m,
        plane_offset_y_m,
    )
    source_plane["planline_drawing_name"] = drawing_name
    source_plane["planline_width_m"] = width_m
    source_plane["planline_height_m"] = height_m
    source_plane["planline_x_rotation_applied_degrees"] = 180

    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "METERS"
    scene.unit_settings.scale_length = 1.0

    blend_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)
    stats = {
        "blender_version": bpy.app.version_string,
        "object": edge_mesh.name,
        "vertices": len(edge_mesh.data.vertices),
        "edges": len(edge_mesh.data.edges),
        "faces": len(edge_mesh.data.polygons),
        "width_m": width_m,
        "height_m": height_m,
        "x_rotation_applied_degrees": 180,
        "source_plane": source_plane.name,
        "source_plane_faces": len(source_plane.data.polygons),
        "source_image": source_png.name,
        "source_image_packed": source_plane["planline_image_packed"],
        "source_plane_offset_m": 0.002,
        "source_plane_alignment_x_m": plane_offset_x_m,
        "source_plane_alignment_y_m": -plane_offset_y_m,
        "source_plane_rotation_x_degrees": 0,
        "source_plane_x_rotation_applied_degrees": 180,
    }
    print("PLANLINE_BLEND_STATS:" + json.dumps(stats, sort_keys=True))


if __name__ == "__main__":
    main()
