from pathlib import Path
import struct
import tempfile
import unittest

from PIL import Image
from gtasa_editor.texdb import Bank, discover, dxt_image, export_png, name_hash


class ReaderTests(unittest.TestCase):
    def test_pvrtc_minimum_size_alpha_and_rle(self):
        for encoding, width in [(0x8C02, 8), (0x8C03, 16)]:
            image = dxt_image(encoding, width, 8, bytes(32))
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 0, 0))
            compressed = bytes([253, 2]) + bytes(16)
            self.assertEqual(dxt_image(encoding, width, 8, compressed, 253).tobytes(), image.tobytes())
        self.assertEqual(dxt_image(0x8C01, 16, 8, bytes(32)).getpixel((0, 0))[3], 255)
        with self.assertRaises(ValueError):
            dxt_image(0x8C02, 8, 8, bytes(16))
        with self.assertRaises(ValueError):
            dxt_image(0x8C02, 12, 8, bytes(128))

    def test_etc1_individual_colors_rle_and_bounds(self):
        # Individual mode, both subblocks use red/blue, table 0 modifier +2.
        for block, expected in [(bytes.fromhex('ff00000000000000'), (255, 2, 2, 255)),
                                (bytes.fromhex('0000ff0000000000'), (2, 2, 255, 255))]:
            self.assertEqual(dxt_image(0x8D64, 4, 4, block).getpixel((0, 0)), expected)
            image = dxt_image(0x8D64, 8, 4, bytes([253, 2]) + block, 253)
            self.assertEqual(image.getpixel((7, 3)), expected)
        with self.assertRaises(ValueError):
            dxt_image(0x8D64, 8, 4, bytes(8))

    def test_rgba4444_colors_alpha_and_rle(self):
        raw = struct.pack('<HHHH', 0xF00F, 0x0F0F, 0x00F5, 0xFFF0)
        image = dxt_image(0x8033, 2, 2, raw)
        self.assertEqual(list(image.get_flattened_data()),
                         [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 85), (255, 255, 255, 0)])
        image = dxt_image(0x8033, 2, 2, bytes([253, 2]) + raw[:4], 253)
        self.assertEqual(image.getpixel((1, 1)), (0, 255, 0, 255))
        with self.assertRaises(ValueError):
            dxt_image(0x8033, 2, 2, raw[:4])

    def test_known_dxt_colors_and_transparency(self):
        for color, expected in [(0xF800, (255, 0, 0, 255)), (0x001F, (0, 0, 255, 255))]:
            block = struct.pack('<HHI', color, 0, 0)
            self.assertEqual(dxt_image(0x83F0, 4, 4, block).getpixel((0, 0)), expected)
        transparent = struct.pack('<HHI', 0, 0xFFFF, 0xFFFFFFFF)
        self.assertEqual(dxt_image(0x83F0, 4, 4, transparent).getpixel((0, 0))[3], 0)
        alpha = bytes([85, 0]) + bytes(6) + struct.pack('<HHI', 0xF800, 0, 0)
        self.assertEqual(dxt_image(0x83F3, 4, 4, alpha).getpixel((0, 0)), (255, 0, 0, 85))

    def test_rle_and_truncation(self):
        block = struct.pack('<HHI', 0xF800, 0, 0)
        image = dxt_image(0x83F0, 8, 4, bytes([253, 2]) + block, 253)
        self.assertEqual(image.getpixel((7, 3)), (255, 0, 0, 255))
        for payload in [b'', bytes([253]), bytes([253, 0]) + block, bytes([253, 2, 1])]:
            with self.assertRaises(ValueError):
                dxt_image(0x83F0, 8, 4, payload, 253)
        with self.assertRaises(ValueError):
            dxt_image(0x83F0, 8, 4, block)

    def test_dxt5_rle_uses_sixteen_byte_segments(self):
        block = bytes([85, 0]) + bytes(6) + struct.pack('<HHI', 0xF800, 0, 0)
        for payload in [bytes([253, 2]) + block, block + bytes([253, 1]) + block]:
            image = dxt_image(0x83F3, 8, 4, payload, 253)
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 85))
            self.assertEqual(image.getpixel((7, 3)), (255, 0, 0, 85))
        with self.assertRaises(ValueError):
            dxt_image(0x83F3, 8, 4, bytes([253, 2]) + block[:8], 253)

    def test_toc_bounds_and_export_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bankdir = root / 'data/texdb/example'
            bankdir.mkdir(parents=True)
            payload = struct.pack('<HHI', 0xF800, 0, 0)
            data = b''.join(struct.pack('<HHHHII', name_hash(name), 0x83F0, 4, 0x8004, 12, 0) + payload for name in ['red', 'neighbor'])
            dat = bankdir / 'example.dxt.dat'
            dat.write_bytes(data)
            toc = dat.with_suffix('.toc')
            toc.write_bytes(struct.pack('<Iii', len(data), 0, 24))
            (bankdir / 'example.txt').write_text('cat=0\n"red" width=4 height=4\n"neighbor" width=4 height=4\n')
            self.assertEqual(discover(root), [toc])
            bank = Bank(toc)
            self.assertEqual(bank.textures[0].end, 24)
            image = bank.image(bank.textures[0])
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))
            with self.assertRaises(ValueError):
                export_png(image, bankdir / 'red.png', bankdir)
            destination = root / 'red.png'
            export_png(image, destination, bankdir)
            with Image.open(destination) as exported:
                self.assertEqual(exported.getpixel((0, 0)), (255, 0, 0, 255))
            with self.assertRaises(FileExistsError):
                export_png(image, destination, bankdir)
            self.assertEqual(dat.read_bytes(), data)
            toc.write_bytes(struct.pack('<Iii', len(data), 0, len(data) + 1))
            with self.assertRaises(ValueError):
                Bank(toc)


if __name__ == '__main__':
    unittest.main()
