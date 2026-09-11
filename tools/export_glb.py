#!/usr/bin/env python3
"""Export a stock Android GTA DFF from an IMG archive as a self-contained GLB."""
import argparse
import io
import json
import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gtasa_editor.models import archive_models, load_model
from gtasa_editor.texdb import Bank


def align4(data: bytearray):
    data.extend(b'\0' * (-len(data) % 4))


def normals(positions, indices):
    result = [0.0] * len(positions)
    for start in range(0, len(indices), 3):
        a, b, c = (indices[start + offset] * 3 for offset in range(3))
        ux, uy, uz = (positions[b + i] - positions[a + i] for i in range(3))
        vx, vy, vz = (positions[c + i] - positions[a + i] for i in range(3))
        normal = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        for vertex in (a, b, c):
            for axis in range(3):
                result[vertex + axis] += normal[axis]
    for start in range(0, len(result), 3):
        length = math.sqrt(sum(value * value for value in result[start:start + 3])) or 1.0
        result[start:start + 3] = [value / length for value in result[start:start + 3]]
    return result


def load_textures(meshes, archive: Path):
    texdb = archive.parent
    banks = [Bank(path) for path in (
        texdb / 'gta3/gta3.dxt.toc', texdb / 'txd/txd.dxt.toc'
    ) if path.is_file()]
    lookups = [{entry.name.casefold(): entry for entry in bank.textures} for bank in banks]
    result = {}
    for name in {mesh.texture_name.casefold() for mesh in meshes if mesh.texture_name}:
        for bank, lookup in zip(banks, lookups):
            entry = lookup.get(name)
            seen = set()
            while entry is not None and entry.offset < 0 and entry.name not in seen:
                seen.add(entry.name)
                entry = lookup.get(entry.props.get('affiliate', '').casefold())
            if entry is not None and entry.supported:
                result[name] = bank.image(entry)
                break
    return result


def export_glb(meshes, images, destination: Path):
    blob = bytearray()
    document = {
        'asset': {'version': '2.0', 'generator': 'gta-sa-editor'},
        'scene': 0,
        'scenes': [{'nodes': []}],
        'nodes': [],
        'meshes': [],
        'materials': [],
        'images': [],
        'textures': [],
        'samplers': [{'magFilter': 9729, 'minFilter': 9987, 'wrapS': 10497, 'wrapT': 10497}],
        'buffers': [{'byteLength': 0}],
        'bufferViews': [],
        'accessors': [],
    }
    material_indices = {}

    def view(values, fmt, target):
        align4(blob)
        offset = len(blob)
        if isinstance(values, bytes):
            blob.extend(values)
        else:
            blob.extend(struct.pack('<' + fmt * len(values), *values))
        index = len(document['bufferViews'])
        item = {'buffer': 0, 'byteOffset': offset, 'byteLength': len(blob) - offset}
        if target is not None:
            item['target'] = target
        document['bufferViews'].append(item)
        return index

    def accessor(view_index, component_type, count, kind, minimum=None, maximum=None):
        item = {'bufferView': view_index, 'componentType': component_type,
                'count': count, 'type': kind}
        if minimum is not None:
            item['min'], item['max'] = minimum, maximum
        index = len(document['accessors'])
        document['accessors'].append(item)
        return index

    texture_indices = {}
    for name, image in images.items():
        encoded = io.BytesIO()
        image.save(encoded, format='PNG', optimize=True)
        image_view = view(encoded.getvalue(), 'c', None)
        document['images'].append({'name': name, 'bufferView': image_view, 'mimeType': 'image/png'})
        texture_indices[name] = len(document['textures'])
        document['textures'].append({'source': len(document['images']) - 1, 'sampler': 0})

    for source in meshes:
        # GTA is Z-up; glTF viewers use Y-up. Winding is reversed with this axis conversion.
        positions = []
        for start in range(0, len(source.positions), 3):
            x, y, z = source.positions[start:start + 3]
            positions.extend((x, z, -y))
        indices = []
        for start in range(0, len(source.indices), 3):
            a, b, c = source.indices[start:start + 3]
            indices.extend((a, c, b))
        vertex_normals = normals(positions, indices)

        position_view = view(positions, 'f', 34962)
        normal_view = view(vertex_normals, 'f', 34962)
        index_view = view(indices, 'I', 34963)
        axes = [positions[axis::3] for axis in range(3)]
        attributes = {
            'POSITION': accessor(position_view, 5126, len(positions) // 3, 'VEC3',
                                 [min(axis) for axis in axes], [max(axis) for axis in axes]),
            'NORMAL': accessor(normal_view, 5126, len(vertex_normals) // 3, 'VEC3'),
        }
        if source.texcoords:
            uv = list(source.texcoords[0])
            # OpenGL DFF previews and glTF disagree on the vertical texture origin.
            for index in range(1, len(uv), 2):
                uv[index] = 1.0 - uv[index]
            attributes['TEXCOORD_0'] = accessor(view(uv, 'f', 34962), 5126, len(uv) // 2, 'VEC2')

        color = tuple(source.diffuse_color)
        material_key = (color, source.texture_name.casefold())
        if material_key not in material_indices:
            material_indices[material_key] = len(document['materials'])
            material = {
                'name': source.texture_name or 'material',
                'pbrMetallicRoughness': {
                    'baseColorFactor': [channel / 255 for channel in color],
                    'metallicFactor': 0.05,
                    'roughnessFactor': 0.72,
                },
                'doubleSided': True,
            }
            if color[3] < 255:
                material['alphaMode'] = 'BLEND'
            texture_index = texture_indices.get(source.texture_name.casefold())
            if texture_index is not None:
                material['pbrMetallicRoughness']['baseColorTexture'] = {'index': texture_index}
            document['materials'].append(material)

        mesh_index = len(document['meshes'])
        document['meshes'].append({
            'name': source.name,
            'primitives': [{
                'attributes': attributes,
                'indices': accessor(index_view, 5125, len(indices), 'SCALAR'),
                'material': material_indices[material_key],
            }],
        })
        document['nodes'].append({'name': source.name, 'mesh': mesh_index})
        document['scenes'][0]['nodes'].append(len(document['nodes']) - 1)

    align4(blob)
    document['buffers'][0]['byteLength'] = len(blob)
    encoded = json.dumps(document, separators=(',', ':')).encode()
    encoded += b' ' * (-len(encoded) % 4)
    total = 12 + 8 + len(encoded) + 8 + len(blob)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(
        struct.pack('<4sII', b'glTF', 2, total)
        + struct.pack('<II', len(encoded), 0x4E4F534A) + encoded
        + struct.pack('<II', len(blob), 0x004E4942) + blob
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('model')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    model_name = args.model.casefold()
    if not model_name.endswith('.dff'):
        model_name += '.dff'
    model = next((item for item in archive_models(args.archive)
                  if item.name.casefold() == model_name), None)
    if model is None:
        raise SystemExit(f'{model_name} não encontrado em {args.archive}')
    model.category = 'Cars'
    meshes = load_model(model)
    images = load_textures(meshes, args.archive)
    export_glb(meshes, images, args.output)
    print(f'{model.name}: {len(meshes)} malhas, '
          f'{sum(mesh.triangle_count for mesh in meshes)} triângulos, '
          f'{len(images)} texturas -> {args.output}')


if __name__ == '__main__':
    main()
