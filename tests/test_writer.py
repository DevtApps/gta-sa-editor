from pathlib import Path
import struct
import tempfile
import unittest

from PIL import Image

from gtasa_editor.texdb import Bank, name_hash
from gtasa_editor.writer import save_copy, replacement_block


class WriterTests(unittest.TestCase):
    def fixture(self, root, mode):
        source = root / 'source'
        source.mkdir()
        block = struct.pack('<HHI', 0xF800, 0, 0)
        if mode == 'larger':
            payload, rle = bytes([253, 2]) + block, 253
        elif mode == 'smaller':
            payload, rle = (bytes([253, 1]) + block) * 2, 253
        else:
            payload, rle = block * 2, 0
        first = struct.pack('<HHHHII', name_hash('wall'), 0x83F0, 8, 0x8004, len(payload)+4, rle) + payload
        neighbor = struct.pack('<HHHHII', name_hash('neighbor'), 0x83F0, 4, 0x8004, 12, 0) + block
        (source / 'bank.dxt.dat').write_bytes(first + neighbor)
        (source / 'bank.dxt.toc').write_bytes(struct.pack('<Iiii', len(first + neighbor), 0, -1, len(first)))
        (source / 'bank.txt').write_bytes(b'cat=0\r\n"wall" width=8 height=4 png=abcdef hassibling=1 unknown=yes\r\n"alias" "affiliate=wall"\r\n"neighbor" width=4 height=4\r\n')
        return Bank(source / 'bank.dxt.toc'), neighbor

    def test_sizes_neighbors_metadata_alias_and_source(self):
        for mode in ['smaller', 'equal', 'larger']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                bank, neighbor = self.fixture(root, mode)
                before = {p.name: p.read_bytes() for p in bank.dat.parent.iterdir()}
                destination = root / 'edited'
                result = save_copy(bank, bank.textures[0], Image.new('RGBA', (8, 4), 'blue'), destination, bank.dat.parent)
                reopened = Bank(result)
                self.assertEqual(reopened.image(reopened.textures[0]).getpixel((0, 0)), (0, 0, 255, 255))
                self.assertEqual(reopened.dat.read_bytes()[reopened.textures[2].offset:], neighbor)
                self.assertEqual(reopened.txt.read_bytes(), before['bank.txt'])
                self.assertEqual(reopened.textures[1].offset, -1)
                self.assertEqual({p.name:p.read_bytes() for p in bank.dat.parent.iterdir()}, before)
                with self.assertRaises(FileExistsError):
                    save_copy(bank, bank.textures[0], Image.new('RGBA', (8, 4), 'blue'), destination, bank.dat.parent)
                with self.assertRaises(ValueError):
                    replacement_block(bank, bank.textures[0], Image.new('RGBA', (4, 4)))

    def test_mips_inferred_despite_header_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bank, _ = self.fixture(root, 'equal')
            entry = bank.textures[0]
            # Four levels: 8x4, 4x2, 2x1, 1x1.
            raw = struct.pack('<HHI', 0xF800, 0, 0) * 5
            header = struct.pack('<HHHHII', name_hash('wall'), 0x83F0, 8, 0x8004, len(raw)+4, 0)
            bank.dat.write_bytes(header + raw)
            entry.end = len(header + raw)
            entry.stored_size = len(raw)+4
            block = replacement_block(bank, entry, Image.new('RGBA', (8, 4), 'blue'))
            self.assertEqual(len(block), len(header + raw))
            self.assertEqual(struct.unpack_from('<H', block, 6)[0], 0x8004)
            for offset in [16, 32, 40, 48]:
                self.assertEqual(struct.unpack_from('<H', block, offset)[0], 0x001F)


if __name__ == '__main__':
    unittest.main()
