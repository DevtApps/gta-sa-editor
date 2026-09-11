from pathlib import Path
import struct
import tempfile
import unittest

from gtasa_editor.catalog import classify, scan
from gtasa_editor.models import archive_models, discover_models
from gtasa_editor.render_rules import component_visible, preview_color, source_score
from gtasa_editor.catalog import CatalogItem
from gtasa_editor.texdb import name_hash


class CatalogTests(unittest.TestCase):
    def test_categories(self):
        for bank, name, expected in [('gta3', 'road01', 'Roads'), ('gta3', 'brickwall', 'Buildings'),
                                     ('player', 'tshirt', 'Clothes'), ('gta_int', 'chair', 'Interiors'),
                                     ('gta3', 'unidentified', 'Miscellaneous')]:
            self.assertEqual(classify(bank, name), expected)

    def test_vehicle_render_and_source_rules(self):
        self.assertEqual(preview_color((60, 255, 0, 255)), (38, 70, 125, 255))
        self.assertEqual(preview_color((204, 204, 204, 253)), (255, 255, 255, 253))
        self.assertTrue(component_visible('bonnet_ok'))
        self.assertFalse(component_visible('bonnet_dam'))
        self.assertFalse(component_visible('chassis_vlo'))
        class Candidate:
            supported = True
        model = type('M', (), {'path': Path('/game/models/lr_cars.img'), 'category': 'Cars'})()
        cars = CatalogItem('paint', Path('/game/Textures/lr_cars.astc_arc'), 'Cars', [(None, Candidate())])
        generic = CatalogItem('paint', Path('/game/texdb/gta3'), 'Cars', [(None, Candidate())])
        self.assertLess(source_score(model, cars), source_score(model, generic))

    def test_variants_group_but_sources_remain_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = []
            for source in [root / 'texdb/test', root / 'data/texdb/test']:
                source.mkdir(parents=True)
                (source / 'test.txt').write_text('cat=0\n"wall" width=4 height=4\n')
                for variant in ['dxt', 'etc']:
                    dat = struct.pack('<HHHHII', name_hash('wall'), 0x83F0, 4, 0x8004, 12, 0) + bytes(8)
                    (source / f'test.{variant}.dat').write_bytes(dat)
                    toc = source / f'test.{variant}.toc'
                    toc.write_bytes(struct.pack('<Ii', len(dat), 0))
                    paths.append(toc)
            items, errors = scan(paths)
            self.assertEqual(errors, [])
            self.assertEqual(len(items), 2)
            self.assertTrue(all(len(item.variants) == 2 for item in items))

    def test_img_boundaries_and_ide_classification(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'gta3.img'
            header = b'VER2' + struct.pack('<I', 1)
            entry = struct.pack('<IHH24s', 1, 1, 1, b'landstal.dff')
            path.write_bytes((header + entry).ljust(4096, b'\0'))
            model = archive_models(path)[0]
            self.assertEqual((model.offset, model.size), (2048, 2048))
            (root / 'vehicles.ide').write_text('cars\n400, landstal, landstal, car\nend\n')
            models, errors = discover_models(root)
            self.assertEqual(errors, [])
            self.assertEqual(models[0].category, 'Cars')
            path.write_bytes((header + struct.pack('<IHH24s', 3, 1, 1, b'bad.dff')).ljust(4096, b'\0'))
            with self.assertRaises(ValueError):
                archive_models(path)
