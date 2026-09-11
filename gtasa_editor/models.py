"""Read-only, bounded IMG directory discovery and DFF scene loading."""
from dataclasses import dataclass
from pathlib import Path
import copy
import math
import struct

from .native_dff import MobileDff as Dff, CompleteMobileDff
from rwfury.dff_parts.models import DffAtomic


@dataclass
class Model:
    name: str
    path: Path
    offset: int
    size: int
    category: str


def model_category(path, name):
    hint = path.stem.casefold()
    if 'car' in hint or 'vehicle' in hint:
        return 'Cars'
    if 'skin' in hint or 'cutscene' in hint:
        return 'Skins'
    if 'interior' in hint or hint == 'gta_int':
        return 'Interiors'
    if 'map' in hint:
        return 'Buildings'
    if hint == 'player':
        return 'Clothes'
    from .catalog import classify
    return classify(hint, name)


def archive_models(path):
    size = path.stat().st_size
    with path.open('rb') as stream:
        header = stream.read(8)
        if len(header) != 8 or header[:4] != b'VER2':
            raise ValueError('IMG não é VER2; formato ainda não suportado.')
        count = struct.unpack_from('<I', header, 4)[0]
        if count > 200000 or 8 + count * 32 > size:
            raise ValueError('Diretório IMG inválido.')
        items = []
        for _ in range(count):
            offset, streaming, sectors, name = struct.unpack('<IHH24s', stream.read(32))
            name = name.split(b'\0', 1)[0].decode('ascii', errors='replace')
            start, length = offset * 2048, (sectors or streaming) * 2048
            if start < 8 + count * 32 or start + length > size:
                raise ValueError('Entrada IMG fora dos limites.')
            if name.casefold().endswith('.dff'):
                items.append(Model(name, path, start, length, model_category(path, name)))
        return items


def discover_models(root, cancelled=lambda: False):
    items, errors = [], []
    definitions = {}
    for ide in root.rglob('*.ide'):
        try:
            section = ''
            for line in ide.read_text(encoding='latin-1').splitlines():
                line = line.split('#', 1)[0].strip()
                if line in ('cars', 'peds', 'end'):
                    section = line
                elif section in ('cars', 'peds'):
                    fields = [field.strip() for field in line.split(',')]
                    if len(fields) >= 3 and fields[0].isdigit():
                        definitions[fields[1].casefold() + '.dff'] = 'Cars' if section == 'cars' else 'Skins'
        except OSError as exc:
            errors.append(f'{ide}: {exc}')
    for path in sorted(root.rglob('*')):
        if cancelled():
            break
        if not path.is_file() or path.suffix.casefold() not in ('.img', '.dff'):
            continue
        try:
            if path.suffix.casefold() == '.img':
                items.extend(archive_models(path))
            else:
                items.append(Model(path.name, path, 0, path.stat().st_size, model_category(path, path.name)))
        except (OSError, ValueError, struct.error) as exc:
            errors.append(f'{path}: {exc}')
    for item in items:
        item.category = definitions.get(item.name.casefold(), item.category)
    return sorted(items, key=lambda item: (item.name.casefold(), str(item.path))), errors


def load_model(model):
    if not 0 < model.size <= 64 * 1024 * 1024:
        raise ValueError('Modelo acima do limite de 64 MiB ou vazio.')
    with model.path.open('rb') as stream:
        stream.seek(model.offset)
        data = stream.read(model.size)
    if len(data) != model.size:
        raise ValueError('Modelo truncado.')
    if len(data) < 12:
        raise ValueError('DFF sem cabeçalho completo.')
    # War Drum's Android converter excludes WDGL descriptor/extra-colour bytes
    # from nested sizes but still advances over them. Those omissions propagate
    # into the outer clump size, which can therefore exceed the IMG allocation
    # by several sectors even though the model is complete. Parsing stays
    # bounded by the allocation and by every WDGL stream descriptor.
    dff = Dff.from_bytes(data)
    frame_names = {frame.name.casefold() for frame in dff.frames}
    if not dff.atomics and {'chassis', 'wheel'} <= frame_names:
        dff = CompleteMobileDff.from_bytes(data)
    if len(dff.geometries) == 2 and not dff.atomics:
        # Stock Android vehicles commonly place the two atomics after the
        # underreported clump boundary. Their fixed pipeline layout is chassis
        # first and reusable right-front wheel second.
        named = {frame.name.casefold(): index for index, frame in enumerate(dff.frames)}
        if 'chassis' in named and 'wheel' in named:
            dff.atomics.extend([
                DffAtomic(frame_index=named['chassis'], geometry_index=0, flags=4),
                DffAtomic(frame_index=named['wheel'], geometry_index=1, flags=4),
            ])
    if dff.geometries and not dff.atomics:
        # Several stock mobile DFF entries end at their IMG sector boundary
        # before the small RpAtomic chunks. Geometry and frame names remain
        # intact. RenderWare associates these single-geometry assets with the
        # frame named after the model; recover that deterministic association.
        model_name = Path(model.name).stem.casefold()
        visible_frames = [index for index, frame in enumerate(dff.frames)
                          if '_dam' not in frame.name.casefold()]
        exact = [index for index in visible_frames
                 if dff.frames[index].name.casefold() == model_name]
        if len(dff.geometries) == 1 and (exact or len(visible_frames) == 1):
            dff.atomics.append(DffAtomic(frame_index=(exact or visible_frames)[0],
                                         geometry_index=0, flags=4))
    # Bake the complete frame hierarchy, including parent transforms, before
    # asking rwfury to export each atomic/material split.
    transforms = {}

    def world(index, visiting):
        if index in transforms:
            return transforms[index]
        if index in visiting or not 0 <= index < len(dff.frames):
            raise ValueError('Hierarquia de frames inválida.')
        frame = dff.frames[index]
        r, p = frame.rotation_matrix, frame.position
        local = [[r[0], r[3], r[6], p[0]], [r[1], r[4], r[7], p[1]],
                 [r[2], r[5], r[8], p[2]], [0, 0, 0, 1]]
        if frame.parent >= 0:
            parent = world(frame.parent, visiting | {index})
            local = [[sum(parent[i][k] * local[k][j] for k in range(4))
                      for j in range(4)] for i in range(4)]
        transforms[index] = local
        return local

    for index in range(len(dff.frames)):
        world(index, set())
    for index, frame in enumerate(dff.frames):
        m = transforms[index]
        frame.rotation_matrix = tuple(m[row][column] for column in range(3) for row in range(3))
        frame.position = tuple(m[row][3] for row in range(3))
    meshes = dff.to_generic_meshes()
    if model.category == 'Cars':
        # GTA stores one wheel atomic below wheel_rf_dummy and clones it to the
        # other wheel dummy frames while setting up CVehicleModelInfo.
        wheel_meshes = [mesh for mesh in meshes if mesh.name.casefold() == 'wheel']
        wheel_frames = {frame.name.casefold(): transforms[index]
                        for index, frame in enumerate(dff.frames)
                        if frame.name.casefold() in ('wheel_rf_dummy', 'wheel_lf_dummy',
                                                    'wheel_rb_dummy', 'wheel_lb_dummy')}
        source = wheel_frames.get('wheel_rf_dummy')
        if source and wheel_meshes:
            clones = []
            for frame_name, matrix in wheel_frames.items():
                if frame_name == 'wheel_rf_dummy':
                    continue
                delta = [matrix[axis][3] - source[axis][3] for axis in range(3)]
                for wheel in wheel_meshes:
                    clone = copy.deepcopy(wheel)
                    clone.name = frame_name.removesuffix('_dummy')
                    clone.transform[12] += delta[0]
                    clone.transform[13] += delta[1]
                    clone.transform[14] += delta[2]
                    clones.append(clone)
            meshes.extend(clones)
    from .render_rules import component_visible, preview_color
    meshes = [mesh for mesh in meshes if component_visible(mesh.name)]
    for mesh in meshes:
        mesh.diffuse_color = preview_color(mesh.diffuse_color, model.category == 'Cars')
    if not meshes:
        raise ValueError('DFF sem geometria ou atomics utilizáveis; pode estar incompleto ou usar uma variante não suportada.')
    if sum(len(mesh.indices) for mesh in meshes) > 3000000:
        raise ValueError('Modelo acima de 1 milhão de triângulos.')
    for mesh in meshes:
        if len(mesh.positions) % 3 or len(mesh.indices) % 3:
            raise ValueError('Geometria desalinhada.')
        if any(i < 0 or i >= mesh.vertex_count for i in mesh.indices):
            raise ValueError('Índice de vértice inválido.')
        if mesh.texcoords and len(mesh.texcoords[0]) != mesh.vertex_count * 2:
            raise ValueError('Coordenadas UV inválidas.')
        t = mesh.transform
        transformed = []
        for start in range(0, len(mesh.positions), 3):
            x, y, z = mesh.positions[start:start + 3]
            transformed.extend(t[axis] * x + t[4 + axis] * y + t[8 + axis] * z + t[12 + axis] for axis in range(3))
        if not all(math.isfinite(value) for value in transformed):
            raise ValueError('Coordenadas não finitas.')
        mesh.positions = transformed
    return meshes
