import io
from pathlib import Path
import struct
import tempfile
import unittest

from PIL import Image
from rwfury.rwbinary import RwBinaryReader
from rwfury.img import Img
from gtasa_editor.native_dff import MobileDff
from gtasa_editor.models import Model, load_model, archive_models
from gtasa_editor.model_io import (export_model, import_model_copy,
                                   import_model_package, model_bytes)
from gtasa_editor.astc import ASTCBank
from gtasa_editor.texdb import Bank, name_hash


def chunk(kind, payload):
    return struct.pack('<III', kind, len(payload), 0x1803FFFF) + payload


def native_triangle():
    geometry = chunk(1, struct.pack('<4I4f2I', 0x01010006, 1, 3, 1, 0, 0, 0, 1, 0, 0))
    material = chunk(7, chunk(1, struct.pack('<4I3f', 0, 0xFFFFFFFF, 0, 0, 1, 0, 1)) + chunk(3, b''))
    geometry += chunk(8, chunk(1, struct.pack('<Ii', 1, -1)) + material)
    descriptors = struct.pack('<I12I', 2, 0, 0, 0, 3, 16, 0, 1, 3, 0, 2, 16, 12)
    vertices = b''.join(struct.pack('<3f2h', *values) for values in [(0, 0, 0, 0, 0), (1, 0, 0, 512, 0), (0, 1, 0, 0, 512)])
    binmesh = struct.pack('<5I3H', 0, 1, 3, 3, 0, 0, 1, 2)
    geometry += chunk(3, chunk(0x50E, binmesh) + chunk(0x510, descriptors + vertices))
    return geometry


def dff_triangle():
    frame = struct.pack('<I12f2i', 1, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, -1, 0)
    clump = chunk(1, struct.pack('<3I', 1, 0, 0))
    clump += chunk(14, chunk(1, frame) + chunk(3, b''))
    clump += chunk(26, chunk(1, struct.pack('<I', 1)) + chunk(15, native_triangle()))
    clump += chunk(20, chunk(1, struct.pack('<4I', 0, 0, 4, 0)) + chunk(3, b''))
    return chunk(16, clump + chunk(3, b''))


class NativeModelTests(unittest.TestCase):
    def test_wdgl_positions_uv_and_indices(self):
        raw = native_triangle()
        geometry = MobileDff()._parse_geometry(RwBinaryReader(io.BytesIO(raw)), len(raw), 0x1803FFFF)
        self.assertEqual(geometry.vertices[1], (1, 0, 0))
        self.assertEqual(geometry.texcoord_sets[0][1], (1, 0))
        self.assertEqual(geometry.bin_mesh.splits[0].indices, [0, 1, 2])
        with self.assertRaises((ValueError, EOFError)):
            MobileDff()._parse_geometry(RwBinaryReader(io.BytesIO(raw[:-3])), len(raw)-3, 0x1803FFFF)

    def test_android_underreported_wdgl_and_outer_sizes(self):
        raw = bytearray(dff_triangle())
        native = raw.index(struct.pack('<I', 0x510))
        declared = struct.unpack_from('<I', raw, native + 4)[0]
        struct.pack_into('<I', raw, native + 4, declared - 12)
        outer = struct.unpack_from('<I', raw, 4)[0]
        struct.pack_into('<I', raw, 4, outer + 4096)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'mobile.dff'
            path.write_bytes(raw)
            model = Model(path.name, path, 0, len(raw), 'Miscellaneous')
            self.assertEqual(sum(mesh.triangle_count for mesh in load_model(model)), 1)

    def test_model_roundtrip_and_neighbor_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            Img.create_v2({'car.dff': dff_triangle(), 'neighbor.bin': b'untouched-neighbor'}, str(source / 'cars.img'))
            model = archive_models(source / 'cars.img')[0]
            self.assertEqual(sum(mesh.triangle_count for mesh in load_model(model)), 1)
            before = model.path.read_bytes()
            export_model(model, {}, [], root / 'export', source)
            self.assertEqual((root / 'export/model.dff').read_bytes(), model_bytes(model))
            result = import_model_copy(model, root / 'export/model.dff', root / 'copy.img', source)
            after = result.path.read_bytes()
            self.assertEqual(before[40:], after[40:len(before)])
            self.assertEqual(model.path.read_bytes(), before)
            self.assertEqual(model_bytes(result), model_bytes(model))
            with self.assertRaises(FileExistsError):
                import_model_copy(model, root / 'export/model.dff', root / 'copy.img', source)
            self.assertEqual(len(load_model(result)), 1)

    def test_astc_archive_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'textures.astc_arc'
            # ASTC void-extent constant transparent black, one 4x4 block.
            payload = bytes.fromhex('fcfdffffffffffff0000000000000000')
            header = b'ASTCARC\0' + struct.pack('<II', 3, 1)
            entry = struct.pack('<32sIIHHBBHI', b'test', 68, 16, 4, 4, 4, 4, 1, 1)
            path.write_bytes(header + entry + payload)
            bank = ASTCBank(path)
            self.assertEqual(bank.image(bank.textures[0]).getpixel((0, 0)), (0, 0, 0, 0))
            path.write_bytes(header + entry)
            bank = ASTCBank(path)
            self.assertFalse(bank.textures[0].supported)

    def test_complete_package_imports_model_and_texture_bank(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'game'
            source.mkdir()
            Img.create_v2({'house.dff': dff_triangle()}, str(source / 'gta3.img'))
            model = archive_models(source / 'gta3.img')[0]
            dxt = struct.pack('<HHI', 0xF800, 0, 0)
            block = struct.pack('<HHHHII', name_hash('wall'), 0x83F0, 4,
                                0x8004, len(dxt) + 4, 0) + dxt
            (source / 'house.dxt.dat').write_bytes(block)
            (source / 'house.dxt.toc').write_bytes(struct.pack('<Ii', len(block), 0))
            (source / 'house.txt').write_text('cat=0\n"wall" width=4 height=4\n')
            bank = Bank(source / 'house.dxt.toc')
            package = root / 'package'
            export_model(model, {'wall': Image.new('RGBA', (4, 4), 'blue')},
                         [], package, source)
            imported, banks = import_model_package(
                model, package, root / 'installed', source,
                {'wall': (bank, bank.textures[0])})
            self.assertEqual(model_bytes(imported), model_bytes(model))
            edited = Bank(banks[0] / 'house.dxt.toc')
            self.assertEqual(edited.image(edited.textures[0]).getpixel((0, 0)),
                             (0, 0, 255, 255))
            self.assertTrue((root / 'installed/import-report.json').is_file())
