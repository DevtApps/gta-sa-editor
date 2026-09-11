from pathlib import Path
import collections, hashlib, json, shutil, struct
from PIL import Image
from mobile_textdb import TextureDatabase
from mobile_textdb.codec import build_texture_block, export_texture_png, hash_name

import argparse
parser = argparse.ArgumentParser(description='Diagnóstico mobile-textdb com fixtures locais')
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
ROOT = args.source.resolve()
OUT = args.output.resolve()
if OUT == ROOT or ROOT in OUT.parents:
    parser.error('A saída deve ficar fora da origem')
if not (ROOT / 'data/texdb/gta3').is_dir():
    parser.error('Origem sem a fixture data/texdb/gta3')
OUT.mkdir(parents=True, exist_ok=False)
report = {'inventory': [], 'replacement': {}, 'synthetic': {}}
for path in sorted((ROOT / 'data/texdb').glob('*/*.toc')):
    if not path.with_suffix('.dat').exists():
        continue
    result = {'path': str(path.relative_to(ROOT))}
    try:
        db = TextureDatabase.open(path.parent, format_ext=path.name.split('.')[-2])
        blocks = list(db.iter_blocks())
        result['textures'] = len(db.list_textures())
        result['encodings'] = dict(collections.Counter(hex(b.encoding) for _, b in blocks))
        result['hash_mismatches'] = sum(b.hash != hash_name(e.name) for e,b in blocks)
        errors = collections.Counter()
        ok = 0
        # Decode bounded representative samples per encoding.
        seen = collections.Counter()
        for entry, block in blocks:
            if seen[block.encoding] >= 5:
                continue
            seen[block.encoding] += 1
            try:
                db.export_image(entry.name)
                ok += 1
            except Exception as exc:
                errors[f'{type(exc).__name__}: {exc}'] += 1
        result.update(decoded_samples=ok, decode_errors=dict(errors))
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    report['inventory'].append(result)

for color, rgba in [('red', (255,0,0,255)), ('blue', (0,0,255,255)), ('alpha', (255,0,0,85))]:
    block = build_texture_block(Image.new('RGBA', (8,8), rgba), color, use_rle=False)
    actual = export_texture_png(block).getpixel((0,0))
    report['synthetic'][color] = {'input': rgba, 'output': actual, 'pass': actual == rgba}

source = ROOT / 'data/texdb/gta3'
copy = OUT / 'gta3'
copy.mkdir(exist_ok=True)
for suffix in ['txt', 'dxt.dat', 'dxt.toc']:
    shutil.copy2(source / f'gta3.{suffix}', copy / f'gta3.{suffix}')
db = TextureDatabase.open(copy)
name = 'ahoodfence2'
before = {e.name: hashlib.sha256(b.to_bytes()).hexdigest() for e,b in db.iter_blocks()}
old_props = dict(db.get(name).props)
old_mip = db.read_block(name).no_mip
offset = db.toc_offsets[db.index_of(name)-1]
old = db.read_block(name)
end = offset + len(old.to_bytes())
neighbor_offset = db.toc_offsets[db.index_of(name)]
original_neighbor_header = bytes(db.dat[neighbor_offset:neighbor_offset+4]).hex()
db.export_image(name).save(OUT / 'ahoodfence2-library.png')
db.replace(name, Image.new('RGBA', (old.width,old.height), (255,0,0,255)))
db.save()
reopened = TextureDatabase.open(copy)
after = {e.name: hashlib.sha256(b.to_bytes()).hexdigest() for e,b in reopened.iter_blocks()}
result = {
    'name': name, 'reopened': True,
    'changed_blocks': [n for n in before if before[n] != after.get(n)],
    'removed_props': sorted(set(old_props)-set(reopened.get(name).props)),
    'no_mip_before': old_mip, 'no_mip_after': reopened.read_block(name).no_mip,
    'original_block_end': end, 'next_block_offset': neighbor_offset,
    'neighbor_header_before': original_neighbor_header,
    'neighbor_header_after': bytes(reopened.dat[neighbor_offset:neighbor_offset+4]).hex(),
    'replacement_pixel': reopened.export_image(name).getpixel((0,0)),
}
report['replacement'] = result
(OUT / 'results.json').write_text(json.dumps(report, indent=2))
print(json.dumps({'synthetic': report['synthetic'], 'replacement': result}, indent=2))
