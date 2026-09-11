"""Bounded reader for the mobile GTA SA texture database layout.

Offsets in TOC, not the library's serialized block length, delimit blocks.
This module is read-only; writer.py creates edited copies separately.
"""
from dataclasses import dataclass, field
from pathlib import Path
import re
import struct

from PIL import Image
import texture2ddecoder

FORMATS = {0x83F0: 'DXT1', 0x83F3: 'DXT5', 0x8D64: 'ETC1',
           0x8C00: 'PVRTC RGB 4bpp', 0x8C03: 'PVRTC RGBA 2bpp',
           0x8C01: 'PVRTC RGB 2bpp', 0x8C02: 'PVRTC RGBA 4bpp',
           0x8033: 'RGBA 4444', 0x8363: 'RGB 565', 0x8050: 'RGBA 32-bit'}
MAX_PIXELS = 4096 * 4096


@dataclass
class Texture:
    name: str
    props: dict[str, str] = field(default_factory=dict)
    offset: int = -1
    end: int = 0
    encoding: int = 0
    width: int = 0
    height: int = 0
    no_mip: bool = False
    stored_size: int = 0
    rle: int = 0
    error: str = ''

    @property
    def format(self):
        return FORMATS.get(self.encoding, f'0x{self.encoding:04X}')

    @property
    def supported(self):
        return not self.error and self.encoding in (0x83F0, 0x83F3, 0x8033, 0x8D64, 0x8C00, 0x8C01, 0x8C02, 0x8C03)

    @property
    def editable(self):
        return not self.error and self.encoding in (0x83F0, 0x83F3, 0x8033)


def discover(root: Path) -> list[Path]:
    """Accept distribution, texdb folder, or individual bank directory."""
    root = root.resolve()
    folders = [root] if root.name == 'texdb' else [root / 'texdb', root / 'data/texdb']
    paths = set(root.glob('*.toc'))
    for folder in folders:
        if folder.is_dir():
            paths.update(folder.glob('*/*.toc'))
    return sorted(paths)


def name_hash(name):
    value = 0
    for byte in name.encode('ascii', errors='replace'):
        value = (value * 33 + byte) & 0xFFFFFFFF
    return ((value + (value >> 5)) & 0xFFFFFFFF) & 0xFFFF


def dxt_image(encoding, width, height, payload, rle=0):
    pvrtc = encoding in (0x8C00, 0x8C01, 0x8C02, 0x8C03)
    if encoding not in (0x83F0, 0x83F3, 0x8033, 0x8D64) and not pvrtc:
        raise ValueError('Prévia indisponível para esta codificação.')
    if min(width, height) <= 0 or width * height > MAX_PIXELS:
        raise ValueError('Dimensões inválidas ou acima do limite de prévia.')
    need = ((width + 3) // 4) * ((height + 3) // 4) * (8 if encoding in (0x83F0, 0x8D64) else 16)
    if encoding == 0x8033:
        need = width * height * 2
    if pvrtc:
        is2bpp = encoding in (0x8C01, 0x8C03)
        if width & (width - 1) or height & (height - 1):
            raise ValueError('PVRTC requer dimensões em potências de dois.')
        storage_width, storage_height = max(width, 16 if is2bpp else 8), max(height, 8)
        need = storage_width * storage_height // (4 if is2bpp else 2)
    if rle:
        # Mobile PVRTC streams group two compressed words per RLE segment.
        segment_size = 16 if pvrtc else {0x83F0: 8, 0x83F3: 16, 0x8033: 4, 0x8D64: 8}[encoding]
        # Decode only the top mip; never expand unbounded runs or inspect neighbors.
        raw = bytearray()
        cursor = 0
        while len(raw) < need:
            if cursor >= len(payload):
                raise ValueError('Dados RLE incompletos.')
            repeat = 1
            if payload[cursor] == (rle & 255):
                if cursor + 2 > len(payload):
                    raise ValueError('Marcador RLE incompleto.')
                repeat = payload[cursor + 1]
                cursor += 2
                if not repeat:
                    raise ValueError('Repetição RLE inválida.')
            if cursor + segment_size > len(payload):
                raise ValueError('Segmento RLE incompleto.')
            raw.extend((payload[cursor:cursor + segment_size] * repeat)[:need - len(raw)])
            cursor += segment_size
        payload = bytes(raw)
    if len(payload) < need:
        raise ValueError('Dados da imagem truncados.')
    if pvrtc:
        pixels = texture2ddecoder.decode_pvrtc(payload[:need], storage_width, storage_height, is2bpp)
        image = Image.frombytes('RGBA', (storage_width, storage_height), pixels, 'raw', 'BGRA')
        if encoding in (0x8C00, 0x8C01):
            image.putalpha(255)
        return image.crop((0, 0, width, height))
    if encoding == 0x8033:
        pixels = bytearray(width * height * 4)
        for index, (value,) in enumerate(struct.iter_unpack('<H', payload[:need])):
            pixels[index * 4:index * 4 + 4] = bytes(
                ((value >> shift) & 15) * 17 for shift in (12, 8, 4, 0))
        return Image.frombytes('RGBA', (width, height), bytes(pixels))
    decoder = {0x83F0: texture2ddecoder.decode_bc1,
               0x83F3: texture2ddecoder.decode_bc3,
               0x8D64: texture2ddecoder.decode_etc1}[encoding]
    pixels = decoder(payload[:need], width, height)
    if encoding == 0x83F0:
        # The native BC1 decoder returns opaque alpha even for selector 3
        # in three-color mode. Restore DXT1's transparent pixels explicitly.
        pixels = bytearray(pixels)
        blocks_w = (width + 3) // 4
        for index in range(need // 8):
            color0, color1, selectors = struct.unpack_from('<HHI', payload, index * 8)
            if color0 > color1:
                continue
            bx, by = index % blocks_w * 4, index // blocks_w * 4
            for pixel in range(16):
                x, y = bx + pixel % 4, by + pixel // 4
                if x < width and y < height and ((selectors >> (2 * pixel)) & 3) == 3:
                    pixels[(y * width + x) * 4 + 3] = 0
        pixels = bytes(pixels)
    return Image.frombytes('RGBA', (width, height), pixels, 'raw', 'BGRA')


class Bank:
    def __init__(self, toc: Path):
        self.toc = toc.resolve()
        self.dat = self.toc.with_suffix('.dat')
        self.txt = self.toc.with_suffix('').with_suffix('.txt')
        size = self.dat.stat().st_size
        raw = self.toc.read_bytes()
        if len(raw) < 4 or len(raw) % 4:
            raise ValueError('TOC truncado ou desalinhado.')
        if struct.unpack_from('<I', raw)[0] != size:
            raise ValueError('Tamanho DAT diverge do TOC.')
        offsets = list(struct.unpack(f'<{(len(raw)-4)//4}i', raw[4:]))
        lines = [line.strip() for line in self.txt.read_text(encoding='utf-8').splitlines() if line.strip()]
        if not lines or not lines[0].startswith('cat=') or len(offsets) != len(lines) - 1:
            raise ValueError('Layout TXT/TOC não reconhecido; índices não serão presumidos.')
        if any(offset < -1 or offset >= size for offset in offsets):
            raise ValueError('TOC contém posição fora do DAT.')
        boundaries = sorted(set(offset for offset in offsets if offset >= 0) | {size})
        ends = dict(zip(boundaries, boundaries[1:]))
        self.textures = []
        with self.dat.open('rb') as stream:
            for line, offset in zip(lines[1:], offsets):
                if line.startswith('cat='):
                    continue
                match = re.fullmatch(r'"([^"]+)"(.*)', line)
                if not match:
                    raise ValueError('Linha TXT não reconhecida.')
                name, rest = match.groups()
                entry = Texture(name, dict(re.findall(r'(\w+)=([^\s"]+)', rest)), offset)
                self.textures.append(entry)
                if offset < 0:
                    entry.error = 'Alias: ' + entry.props.get('affiliate', 'sem bloco próprio')
                    continue
                entry.end = ends[offset]
                try:
                    if entry.end - offset < 16:
                        raise ValueError('Cabeçalho truncado.')
                    stream.seek(offset)
                    header = stream.read(16)
                    hashed, entry.encoding, entry.width, height, entry.stored_size, entry.rle = struct.unpack('<HHHHII', header)
                    entry.height = height & 0x7FFF
                    entry.no_mip = bool(height & 0x8000)
                    if hashed != name_hash(name):
                        raise ValueError('Hash do nome diverge do cabeçalho.')
                    if not entry.width or not entry.height:
                        raise ValueError('Dimensões inválidas.')
                    # Observed files count four RLE-header bytes in stored_size.
                    # Allow both observed size conventions, bounded by TOC either way.
                    available = entry.end - offset - 16
                    if entry.stored_size not in (available, available + 4):
                        raise ValueError('Tamanho do bloco não corresponde aos limites TOC.')
                except (ValueError, struct.error) as exc:
                    entry.error = str(exc)

    def image(self, entry: Texture):
        if entry.error:
            raise ValueError(entry.error)
        if not entry.supported:
            raise ValueError(f'Prévia ainda não suportada: {entry.format}.')
        length = entry.end - entry.offset - 16
        if length > 64 * 1024 * 1024:
            raise ValueError('Bloco acima do limite de prévia (64 MiB).')
        with self.dat.open('rb') as stream:
            stream.seek(entry.offset + 16)
            payload = stream.read(length)
        return dxt_image(entry.encoding, entry.width, entry.height, payload, entry.rle)


def export_png(image, destination: Path, source_root: Path):
    destination = destination.resolve()
    if destination.is_relative_to(source_root.resolve()):
        raise ValueError('Escolha uma pasta de exportação fora da origem.')
    # Exclusive creation also protects existing files, including reference copies.
    with destination.open('xb') as stream:
        image.save(stream, format='PNG')
