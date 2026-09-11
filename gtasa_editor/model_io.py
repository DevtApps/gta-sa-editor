"""Lossless DFF export and single-model replacement into an IMG copy."""
import json
from pathlib import Path
import shutil
import struct

from .models import Model, archive_models, load_model
from .writer import save_bank_copy


def model_bytes(model):
    if not 0 < model.size <= 64 * 1024 * 1024:
        raise ValueError('Tamanho DFF fora dos limites.')
    with model.path.open('rb') as stream:
        stream.seek(model.offset)
        data = stream.read(model.size)
    if len(data) != model.size:
        raise ValueError('DFF truncado.')
    return data


def export_model(model, images, material_report, destination, source_root, bindings=None):
    destination = Path(destination).resolve()
    if destination.is_relative_to(Path(source_root).resolve()):
        raise ValueError('Exporte para uma pasta nova fora da origem.')
    data = model_bytes(model)
    destination.mkdir()
    try:
        (destination / 'model.dff').write_bytes(data)
        materials = {}
        for index, (name, image) in enumerate(images.items()):
            if bindings and name in bindings:
                bank, entry = bindings[name]
                image = bank.image(entry)
            filename = f'texture-{index:04d}.png'
            image.save(destination / filename)
            materials[name] = filename
        (destination / 'manifest.json').write_text(json.dumps({
            'original_name': model.name, 'source': str(model.path), 'textures': materials,
            'material_report': material_report,
            'note': 'DFF original completo, com plugins e rig preservados. PNGs decodificados na resolução da fonte quando disponíveis.'
        }, ensure_ascii=False, indent=2))
    except Exception:
        shutil.rmtree(destination)
        raise


def import_model_copy(model, replacement, destination, source_root):
    replacement, destination = Path(replacement).resolve(), Path(destination).resolve()
    if destination == replacement or destination.is_relative_to(Path(source_root).resolve()):
        raise ValueError('Salve uma cópia fora da origem, sem sobrescrever o DFF importado.')
    incoming = Model(replacement.name, replacement, 0, replacement.stat().st_size, model.category)
    load_model(incoming)  # Validate renderable geometry before creating any output.
    data = model_bytes(incoming)
    if model.path.suffix.casefold() != '.img':
        with destination.open('xb') as stream:
            stream.write(data)
        return Model(destination.name, destination, 0, len(data), model.category)
    entries = archive_models(model.path)
    if not any((entry.name, entry.offset, entry.size) == (model.name, model.offset, model.size) for entry in entries):
        raise ValueError('O IMG mudou. Abra-o novamente.')
    with model.path.open('rb') as source:
        header = source.read(8)
        count = struct.unpack_from('<I', header, 4)[0]
        matches = []
        for index in range(count):
            row = source.read(32)
            name = row[8:].split(b'\0', 1)[0].decode('ascii', errors='replace')
            if name == model.name:
                matches.append((index, row))
    if len(matches) != 1:
        raise ValueError('Nome de modelo ambíguo no IMG.')
    index, row = matches[0]
    sectors = (len(data) + 2047) // 2048
    if sectors > 65535:
        raise ValueError('DFF acima do limite do índice IMG.')
    with destination.open('xb') as target:
        try:
            with model.path.open('rb') as source:
                shutil.copyfileobj(source, target)
            offset = (target.tell() + 2047) // 2048
            target.write(bytes(offset * 2048 - target.tell()))
            target.write(data)
            target.write(bytes(sectors * 2048 - len(data)))
            # Appending preserves every original data block. Only this directory
            # row changes; unused old model bytes remain in the experimental copy.
            old_size = struct.unpack_from('<H', row, 6)[0]
            target.seek(8 + index * 32)
            target.write(struct.pack('<IHH', offset, sectors, sectors if old_size else 0) + row[8:])
        except Exception:
            target.close()
            destination.unlink()
            raise
    return next(entry for entry in archive_models(destination) if entry.name == model.name)


def import_model_package(model, package, destination, source_root, bindings):
    """Import an exported DFF+PNG package into independent model/bank copies."""
    from PIL import Image
    package, destination = Path(package).resolve(), Path(destination).resolve()
    source_root = Path(source_root).resolve()
    if destination.is_relative_to(source_root) or destination.exists():
        raise ValueError('Escolha uma pasta nova fora da origem.')
    manifest_path = package / 'manifest.json'
    model_path = package / 'model.dff'
    if not manifest_path.is_file() or not model_path.is_file():
        raise ValueError('Pacote incompleto: manifest.json e model.dff sao obrigatorios.')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    textures = manifest.get('textures')
    if not isinstance(textures, dict) or len(textures) > 4096:
        raise ValueError('Lista de texturas invalida no manifesto.')
    missing = sorted(name for name in textures if name.casefold() not in bindings)
    if missing:
        raise ValueError('Texturas sem destino nos bancos carregados: ' + ', '.join(missing[:12]))
    grouped = {}
    opened = []
    for name, filename in textures.items():
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError(f'Caminho de textura invalido: {name}.')
        image_path = package / filename
        if not image_path.is_file() or image_path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError(f'PNG ausente ou acima do limite: {filename}.')
        image = Image.open(image_path)
        image.load()
        opened.append(image)
        bank, entry = bindings[name.casefold()]
        grouped.setdefault(bank.toc, (bank, []) )[1].append((entry, image))
    # Validate the incoming DFF and all replacement blocks before creating output.
    incoming = Model(model_path.name, model_path, 0, model_path.stat().st_size, model.category)
    load_model(incoming)
    for bank, replacements in grouped.values():
        for entry, image in replacements:
            from .writer import replacement_block
            replacement_block(bank, entry, image)
    destination.mkdir()
    try:
        suffix = '.img' if model.path.suffix.casefold() == '.img' else '.dff'
        model_output = destination / (model.path.stem + '-editado' + suffix)
        imported = import_model_copy(model, model_path, model_output, source_root)
        bank_outputs = []
        for index, (bank, replacements) in enumerate(grouped.values(), 1):
            bank_folder = destination / f'texturas-{index:02d}-{bank.toc.stem}'
            save_bank_copy(bank, replacements, bank_folder, source_root)
            bank_outputs.append(bank_folder)
        (destination / 'import-report.json').write_text(json.dumps({
            'model': str(model_output), 'texture_banks': [str(path) for path in bank_outputs],
            'textures': sorted(textures), 'source_package': str(package)
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        return imported, bank_outputs
    except Exception:
        shutil.rmtree(destination)
        raise
    finally:
        for image in opened:
            image.close()
