"""Experimental replacement into a new, independent bank directory."""
import io
from pathlib import Path
import shutil
import struct

from PIL import Image

from .texdb import Bank, MAX_PIXELS, name_hash


def unpack_payload(payload, encoding, indicator):
    if not indicator:
        return payload
    segment = {0x83F0: 8, 0x83F3: 16, 0x8033: 4}[encoding]
    output = bytearray()
    cursor = 0
    while cursor < len(payload):
        count = 1
        if payload[cursor] == (indicator & 255):
            if cursor + 2 > len(payload):
                raise ValueError('RLE truncado.')
            count = payload[cursor + 1]
            cursor += 2
        if not count or cursor + segment > len(payload):
            raise ValueError('Segmento RLE inválido.')
        if len(output) + count * segment > MAX_PIXELS * 8:
            raise ValueError('Imagem descomprimida acima do limite.')
        output.extend(payload[cursor:cursor + segment] * count)
        cursor += segment
    return bytes(output)


def level_size(width, height, encoding):
    if encoding == 0x8033:
        return width * height * 2
    return ((width + 3) // 4) * ((height + 3) // 4) * (8 if encoding == 0x83F0 else 16)


def encode_level(image, encoding):
    if encoding == 0x8033:
        result = bytearray()
        for r, g, b, a in image.get_flattened_data():
            result.extend(struct.pack('<H', (r >> 4) << 12 | (g >> 4) << 8 | (b >> 4) << 4 | (a >> 4)))
        return bytes(result)
    buffer = io.BytesIO()
    image.save(buffer, format='DDS', pixel_format='DXT1' if encoding == 0x83F0 else 'DXT5')
    data = buffer.getvalue()
    if data[:4] != b'DDS ' or len(data) != 128 + level_size(*image.size, encoding):
        raise ValueError('O codificador retornou um tamanho inesperado.')
    return data[128:]


def replacement_block(bank, entry, image):
    if not entry.editable:
        raise ValueError('Esta textura não pode ser substituída.')
    if image.size != (entry.width, entry.height):
        raise ValueError(f'O PNG deve ter exatamente {entry.width} × {entry.height} pixels.')
    image = image.convert('RGBA')
    if entry.encoding == 0x83F0 and image.getchannel('A').getextrema()[0] != 255:
        raise ValueError('A importação DXT1 requer PNG opaco nesta versão. Use uma textura DXT5 para transparência.')
    with bank.dat.open('rb') as stream:
        stream.seek(entry.offset + 16)
        original = stream.read(entry.end - entry.offset - 16)
    raw = unpack_payload(original, entry.encoding, entry.rle)
    # Some real banks carry a complete mip chain despite the header flag.
    # Infer the exact existing levels from the bounded payload, preserving the flag.
    dimensions = []
    consumed = 0
    width, height = image.size
    while True:
        dimensions.append((width, height))
        consumed += level_size(width, height, entry.encoding)
        if consumed == len(raw):
            break
        if consumed > len(raw) or (width, height) == (1, 1):
            raise ValueError('Layout de mipmaps não reconhecido; gravação recusada.')
        width, height = max(1, width // 2), max(1, height // 2)
    levels = []
    for size in dimensions:
        level = image if size == image.size else image.resize(size, Image.Resampling.LANCZOS)
        levels.append(encode_level(level, entry.encoding))
    payload = b''.join(levels)
    size_bias = entry.stored_size - len(original)
    header = struct.pack('<HHHHII', name_hash(entry.name), entry.encoding, entry.width,
                         entry.height | (0x8000 if entry.no_mip else 0), len(payload) + size_bias, 0)
    return header + payload


def save_copy(bank, entry, image, destination: Path, source_root: Path):
    destination = destination.resolve()
    if destination.is_relative_to(source_root.resolve()) or destination.is_relative_to(bank.dat.parent):
        raise ValueError('Escolha uma pasta nova fora da origem.')
    # Refresh before using offsets: fail if files changed while the UI was open.
    fresh = Bank(bank.toc)
    matches = [item for item in fresh.textures if item.name == entry.name]
    if len(matches) != 1 or matches[0] != entry:
        raise ValueError('O banco mudou ou o nome é ambíguo. Abra o banco novamente.')
    block = replacement_block(fresh, entry, image)
    toc_raw = fresh.toc.read_bytes()
    offsets = list(struct.unpack(f'<{(len(toc_raw)-4)//4}i', toc_raw[4:]))
    if offsets.count(entry.offset) != 1:
        raise ValueError('Bloco compartilhado por múltiplos índices; substituição indisponível.')
    delta = len(block) - (entry.end - entry.offset)
    new_size = fresh.dat.stat().st_size + delta
    adjusted = [offset + delta if offset >= entry.end else offset for offset in offsets]
    new_toc = struct.pack('<I', new_size) + struct.pack(f'<{len(adjusted)}i', *adjusted)
    destination.mkdir()  # Exclusive: existing directories are never reused.
    try:
        # Keep all other variants and metadata. The selected thumbnail cache is
        # optional and omitted because it would show the previous texture.
        thumbnail = fresh.toc.with_suffix('.tmb')
        for path in fresh.dat.parent.iterdir():
            if path.is_file() and path not in (fresh.dat, fresh.toc, thumbnail):
                shutil.copyfile(path, destination / path.name)
        with fresh.dat.open('rb') as source, (destination / fresh.dat.name).open('xb') as target:
            remaining = entry.offset
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError('DAT truncado durante a cópia.')
                target.write(chunk)
                remaining -= len(chunk)
            target.write(block)
            source.seek(entry.end)
            shutil.copyfileobj(source, target)
        (destination / fresh.toc.name).write_bytes(new_toc)
        reopened = Bank(destination / fresh.toc.name)
        selected = next(item for item in reopened.textures if item.name == entry.name)
        reopened.image(selected)
        return reopened.toc
    except Exception:
        shutil.rmtree(destination)
        raise


def save_bank_copy(bank, replacements, destination: Path, source_root: Path):
    """Write several texture replacements into one independent bank copy."""
    destination = destination.resolve()
    if destination.is_relative_to(source_root.resolve()) or destination.is_relative_to(bank.dat.parent):
        raise ValueError('Escolha uma pasta nova fora da origem.')
    fresh = Bank(bank.toc)
    by_name = {entry.name.casefold(): entry for entry in fresh.textures}
    selected = []
    for entry, image in replacements:
        current = by_name.get(entry.name.casefold())
        if current is None or current != entry:
            raise ValueError(f'O banco mudou ou a textura {entry.name} ficou ambigua.')
        selected.append((current, replacement_block(fresh, current, image)))
    if len({entry.offset for entry, _ in selected}) != len(selected):
        raise ValueError('O pacote tenta substituir o mesmo bloco de textura mais de uma vez.')
    toc_raw = fresh.toc.read_bytes()
    offsets = list(struct.unpack(f'<{(len(toc_raw)-4)//4}i', toc_raw[4:]))
    blocks = {entry.offset: (entry, block) for entry, block in selected}
    destination.mkdir()
    try:
        thumbnail = fresh.toc.with_suffix('.tmb')
        for path in fresh.dat.parent.iterdir():
            if path.is_file() and path not in (fresh.dat, fresh.toc, thumbnail):
                shutil.copyfile(path, destination / path.name)
        adjusted = list(offsets)
        output_size = 0
        with fresh.dat.open('rb') as source, (destination / fresh.dat.name).open('xb') as target:
            cursor = 0
            for offset in sorted(blocks):
                entry, block = blocks[offset]
                shutil.copyfileobj(io.BytesIO(source.read(offset - cursor)), target)
                if source.tell() != offset:
                    raise ValueError('DAT truncado durante a copia do pacote.')
                new_offset = target.tell()
                for index, old in enumerate(offsets):
                    if old == entry.offset:
                        adjusted[index] = new_offset
                target.write(block)
                source.seek(entry.end)
                cursor = entry.end
            shutil.copyfileobj(source, target)
            output_size = target.tell()
        # Shift untouched entries according to all preceding replacements.
        for index, old in enumerate(offsets):
            if old < 0 or old in blocks:
                continue
            adjusted[index] = old + sum(len(block) - (entry.end - entry.offset)
                                        for entry, block in selected if entry.end <= old)
        new_toc = struct.pack('<I', output_size) + struct.pack(f'<{len(adjusted)}i', *adjusted)
        (destination / fresh.toc.name).write_bytes(new_toc)
        reopened = Bank(destination / fresh.toc.name)
        lookup = {entry.name.casefold(): entry for entry in reopened.textures}
        for entry, _ in selected:
            reopened.image(lookup[entry.name.casefold()])
        return reopened.toc
    except Exception:
        shutil.rmtree(destination)
        raise
