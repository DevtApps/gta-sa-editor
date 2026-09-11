"""Catalog by source and name; categories are heuristics, not model ownership."""
from dataclasses import dataclass, field
from pathlib import Path
import re

from .texdb import Bank, Texture

CATEGORIES = ('All', 'Buildings', 'Cars', 'Skins', 'Roads', 'Clothes',
              'Interiors', 'Vegetation', 'UI', 'Miscellaneous')
RULES = (
    ('Cars', r'vehicle|carbody|carpaint|carwheel|tyre|tire|wheel|windscreen|plateback|licenseplate|headlight|taillight'),
    ('Roads', r'road|asphalt|pavement|sidewalk|tarmac|crosswalk|freeway|hiway|highway|lanemark|kerb'),
    ('Vegetation', r'grass|bark|tree|leaves|leaf|foliage|palm|hedge|bush|flower'),
    ('Clothes', r'tshirt|t-shirt|jeans|trouser|jacket|sneaker|shirt|hoody|hoodie|vest|denim|bandana'),
    ('Buildings', r'brick|wall|roof|window|building|house|concrete|plaster|shingle|garage|fence|shopfront|storefront'),
)


def classify(bank_name, name):
    bank_name, name = bank_name.casefold(), name.casefold()
    if bank_name.startswith('lr_'):
        for key, category in [('cars', 'Cars'), ('skins', 'Skins'), ('maps', 'Buildings'),
                              ('interiors', 'Interiors'), ('gui', 'UI'), ('mobile', 'UI'), ('radar', 'UI')]:
            if key in bank_name:
                return category
    if bank_name in ('mobile', 'menu', 'gtasa'):
        return 'UI'
    if bank_name in ('player', 'playerhi'):
        return 'Skins' if re.search(r'tatt|face|head|torso|hand|leg|body', name) else 'Clothes'
    if bank_name == 'gta_int':
        return 'Interiors'
    if re.match(r'^(?:[bw][mf](?:y|o)|[hsa][mf](?:y|o))', name):
        return 'Skins'
    for category, pattern in RULES:
        if re.search(pattern, name):
            return category
    return 'Miscellaneous'


@dataclass
class CatalogItem:
    name: str
    source: Path
    category: str
    variants: list[tuple[Bank, Texture]] = field(default_factory=list)


def scan(paths, progress=lambda done, total: None, cancelled=lambda: False):
    groups = {}
    errors = []
    for index, path in enumerate(paths):
        if cancelled():
            return [], []
        try:
            if path.suffix == '.astc_arc':
                from .astc import ASTCBank
                bank = ASTCBank(path)
            else:
                bank = Bank(path)
            for entry in bank.textures:
                key = (bank.txt, entry.name)
                if key not in groups:
                    groups[key] = CatalogItem(entry.name, bank.txt if path.suffix == '.astc_arc' else bank.txt.parent,
                                              classify(bank.txt.stem, entry.name))
                groups[key].variants.append((bank, entry))
            if path.suffix == '.astc_arc':
                missing = sum(bool(entry.error) for entry in bank.textures)
                if missing:
                    errors.append(f'{path}: {missing}/{len(bank.textures)} imagens indisponíveis; dados fora dos limites ou dimensões inválidas.')
        except (OSError, ValueError) as exc:
            errors.append(f'{path}: {exc}')
        progress(index + 1, len(paths))
    items = sorted(groups.values(), key=lambda item: (item.name.casefold(), str(item.source)))
    for item in items:
        # Preferred preview only; this does not imply which variant the game loads.
        item.variants.sort(key=lambda pair: (not pair[1].editable, not pair[1].supported, str(pair[0].toc)))
    return items, errors
