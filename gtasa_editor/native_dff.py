"""Bounded WDGL geometry adapter for rwfury's scene/material reader.

Binary layout reference: DragonFF gtaLib/native_wdgl.py (attribute IDs/types,
24-byte descriptors, fixed-point UV /512). This adapter does not write DFFs.
"""
import struct
from rwfury.dff import Dff
from rwfury.dff_parts.models import DffGeometry, BinMeshPLG, BinMeshSplit


class MobileDff(Dff):
    recover_containers = False

    def _parse_clump(self, reader, header, clump_end):
        if not self.recover_containers:
            return super()._parse_clump(reader, header, clump_end)
        # WDGL's underreported payload sizes are propagated to the containing
        # clump in the Android build. An IMG entry is already a hard boundary.
        # Let the regular parser walk its declared child chunks to that boundary.
        original_size = header.size
        header.size = reader.file_size - reader.tell()
        try:
            return super()._parse_clump(reader, header, reader.file_size)
        finally:
            header.size = original_size

    def _parse_geometry_list(self, reader, chunk_end):
        if not self.recover_containers:
            return super()._parse_geometry_list(reader, chunk_end)
        header = reader.read_chunk_header()
        if header.id != 1 or header.size < 4:
            raise ValueError('Lista de geometrias invalida.')
        count = reader.read_u32()
        if count > 100000:
            raise ValueError('Lista de geometrias acima dos limites.')
        for _ in range(count):
            # Android geometry chunks may leave up to a small alignment gap
            # after their real WDGL end. Locate the next typed RW header rather
            # than trusting the contradictory aggregate byte sizes.
            found = False
            search_end = min(reader.file_size - 12, reader.tell() + 2048)
            while reader.tell() <= search_end:
                position = reader.tell()
                candidate = reader.read_chunk_header()
                if candidate.id == 15 and 0 < candidate.size <= reader.file_size - reader.tell():
                    found = True
                    self.geometries.append(self._parse_geometry(
                        reader, min(reader.file_size, reader.tell() + candidate.size),
                        candidate.version))
                    break
                reader.seek(position + 1)
            if not found:
                break
        # Align the parent walker with the first clump child after the list.
        search_end = min(reader.file_size - 12, reader.tell() + 2048)
        while reader.tell() <= search_end:
            position = reader.tell()
            candidate = reader.read_chunk_header()
            if candidate.id in (20, 3) and 0 <= candidate.size <= reader.file_size - reader.tell():
                reader.seek(position)
                break
            reader.seek(position + 1)

    @staticmethod
    def _wdgl_payload_size(reader, declared_size, extension_end, vertices):
        """Return the effective War Drum payload size.

        Some Android assets exclude the 24-byte descriptor table from the
        Native Data PLG size. Other exporters include it, so only extend a
        payload when the declared bytes cannot contain the described streams.
        """
        start = reader.tell()
        if declared_size < 4 or start + declared_size > extension_end:
            return declared_size
        data = reader.read_bytes(declared_size)
        reader.seek(start)
        count = struct.unpack_from('<I', data)[0]
        if count > 16 or len(data) < 4 + count * 24:
            return declared_size
        type_sizes = {0: 4, 1: 1, 2: 1, 3: 2, 4: 2}
        required = 4 + count * 24
        for index in range(count):
            _, kind, _, components, stride, offset = struct.unpack_from(
                '<6I', data, 4 + index * 24)
            if kind not in type_sizes or not 1 <= components <= 4 or not stride:
                return declared_size
            stream_end = (4 + count * 24 + offset +
                          max(0, vertices - 1) * stride +
                          components * type_sizes[kind])
            required = max(required, stream_end)
        if required <= declared_size:
            return required
        # The stock archives also omit interleaved extra-colour bytes on some
        # assets. Consume exactly the final described byte, rather than using
        # either malformed chunk-size convention as the stream boundary.
        return required if start + required <= extension_end else declared_size

    def _parse_geometry(self, reader, chunk_end, version):
        start = reader.tell()
        header = reader.read_chunk_header()
        raw = reader.read_bytes(header.size)
        if header.id != 1 or len(raw) < 16:
            raise ValueError('Cabeçalho de geometria inválido.')
        flags, triangles, vertices, morphs = struct.unpack_from('<4I', raw)
        if not flags & 0x01000000:
            reader.seek(start)
            return super()._parse_geometry(reader, chunk_end, version)
        if vertices > 1000000 or morphs > 64:
            raise ValueError('Geometria nativa acima dos limites.')
        geom = DffGeometry()
        geom.flags = flags
        geom.num_uv_sets = (flags >> 16) & 255
        native = None
        while reader.tell() + 12 <= chunk_end:
            child = reader.read_chunk_header()
            end = reader.tell() + child.size
            if end > chunk_end:
                raise ValueError('Chunk de geometria fora dos limites.')
            if child.id == 8:
                self._parse_material_list(reader, end, geom)
            elif child.id == 3:
                extension_consumed = reader.tell()
                while reader.tell() + 12 <= end:
                    plugin = reader.read_chunk_header()
                    plugin_size = plugin.size
                    if plugin.id == 0x510:
                        plugin_size = self._wdgl_payload_size(
                            reader, plugin_size, reader.file_size, vertices)
                    plugin_end = reader.tell() + plugin_size
                    if plugin_end > reader.file_size:
                        # Mobile IMG entries are sector-sized and some native
                        # geometry extensions end with non-chunk padding. The
                        # game stops walking plugins at this boundary too.
                        reader.seek(end)
                        break
                    data = reader.read_bytes(plugin_size)
                    extension_consumed = max(extension_consumed, plugin_end)
                    if plugin.id == 0x510:
                        native = data
                        break
                    elif plugin.id == 0x50E:
                        if len(data) < 12:
                            raise ValueError('BinMesh nativo truncado.')
                        mode, count, total = struct.unpack_from('<III', data)
                        if count > 65536 or total > 3000000:
                            raise ValueError('BinMesh acima dos limites.')
                        cursor = 12
                        bm = BinMeshPLG()
                        bm.flags = mode
                        for _ in range(count):
                            n, material = struct.unpack_from('<II', data, cursor)
                            cursor += 8
                            if n > total or cursor + n * 2 > len(data):
                                raise ValueError('Índices nativos truncados.')
                            indices = list(struct.unpack_from(f'<{n}H', data, cursor))
                            cursor += n * 2
                            bm.splits.append(BinMeshSplit(material_index=material, indices=indices))
                        geom.bin_mesh = bm
                end = extension_consumed
            reader.seek(end)
            # RenderWare geometry extensions are the final geometry child.
            # Several Android assets overreport the geometry container after
            # compensating for WDGL, so walking to its nominal end would eat
            # the following geometry header.
            if child.id == 3:
                break
        if native is None or len(native) < 4:
            raise ValueError('Dados WDGL ausentes.')
        count = struct.unpack_from('<I', native)[0]
        base = 4 + 24 * count
        if count > 16 or base > len(native):
            raise ValueError('Descritores WDGL inválidos.')
        types = {0: ('f', 4, 1), 1: ('b', 1, 127), 2: ('B', 1, 255),
                 3: ('h', 2, 32767), 4: ('H', 2, 65535)}
        for index in range(count):
            attr, kind, normalized, components, stride, offset = struct.unpack_from('<6I', native, 4 + index * 24)
            if kind not in types or not 1 <= components <= 4 or not stride:
                raise ValueError('Atributo WDGL não reconhecido.')
            code, size, divisor = types[kind]
            stream_end = base + offset + max(0, vertices - 1) * stride + components * size
            if vertices and stream_end > len(native):
                raise ValueError(
                    f'Vertices WDGL fora dos limites '
                    f'(atributo={attr}, vertices={vertices}, necessario={stream_end}, disponivel={len(native)}).')
            values = [struct.unpack_from(f'<{components}{code}', native, base + offset + i * stride)
                      for i in range(vertices)]
            if normalized:
                values = [tuple(max(-1, x / divisor) for x in row) for row in values]
            if attr == 0:
                geom.vertices = values
            elif attr == 1:
                geom.texcoord_sets = [[tuple(x / 512 for x in row) for row in values]]
            elif attr == 2:
                geom.normals = values
            elif attr == 3:
                geom.vertex_colors = [tuple(round(x * 255) if normalized else x for x in row) for row in values]
        if len(geom.vertices) != vertices:
            raise ValueError('Posições WDGL ausentes.')
        return geom


class CompleteMobileDff(MobileDff):
    """Second-pass reader for models with recognisable truncated containers."""
    recover_containers = True
