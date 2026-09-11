"""Read-only ASTCARC v3 index used by mobile GTA SA (52-byte entries)."""
from pathlib import Path
import struct
from PIL import Image
import texture2ddecoder
from .texdb import Texture, MAX_PIXELS


class ASTCTexture(Texture):
    @property
    def format(self):
        return f'ASTC {self.props["block_x"]}×{self.props["block_y"]}'

    @property
    def supported(self):
        return not self.error

    @property
    def editable(self):
        return False


class ASTCBank:
    def __init__(self, path):
        self.toc = self.dat = self.txt = Path(path).resolve()
        size = self.dat.stat().st_size
        self.textures = []
        with self.dat.open('rb') as stream:
            header = stream.read(16)
            if len(header) != 16 or header[:8] != b'ASTCARC\0':
                raise ValueError('Assinatura ASTCARC inválida.')
            version, count = struct.unpack_from('<II', header, 8)
            if version != 3 or count > 200000 or 16 + count * 52 > size:
                raise ValueError('Índice ASTCARC não reconhecido ou incompleto.')
            for _ in range(count):
                name, offset, length, width, height, bx, by, mips, flags = struct.unpack('<32sIIHHBBHI', stream.read(52))
                name = name.split(b'\0', 1)[0].decode('utf-8', errors='replace')
                entry = ASTCTexture(name, {'block_x': bx, 'block_y': by, 'mipmaps': mips, 'flags': hex(flags)},
                                    offset=offset, end=offset + length, width=width, height=height,
                                    stored_size=length, no_mip=mips <= 1)
                if offset < 16 + count * 52 or offset + length > size:
                    entry.error = 'Dados ausentes: o índice aponta além do fim do pacote ASTC.'
                valid_blocks = {(4, 4), (5, 4), (5, 5), (6, 5), (6, 6), (8, 5), (8, 6), (8, 8),
                                (10, 5), (10, 6), (10, 8), (10, 10), (12, 10), (12, 12)}
                if not (0 < width * height <= MAX_PIXELS and (bx, by) in valid_blocks):
                    entry.error = 'Dimensões ASTC inválidas.'
                self.textures.append(entry)

    def image(self, entry):
        if entry.error:
            raise ValueError(entry.error)
        bx, by = entry.props['block_x'], entry.props['block_y']
        need = ((entry.width + bx - 1) // bx) * ((entry.height + by - 1) // by) * 16
        if need > entry.stored_size:
            raise ValueError('Imagem ASTC truncada.')
        with self.dat.open('rb') as stream:
            stream.seek(entry.offset)
            payload = stream.read(need)
        if len(payload) != need:
            raise ValueError('Imagem ASTC truncada.')
        pixels = texture2ddecoder.decode_astc(payload, entry.width, entry.height, bx, by)
        return Image.frombytes('RGBA', (entry.width, entry.height), pixels, 'raw', 'BGRA')
