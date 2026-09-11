"""GTA SA vehicle preview rules derived from DragonFF/gta-reversed semantics."""
VEHICLE_MARKERS = {
    (60, 255, 0, 255): ('primary', (38, 70, 125, 255)),
    (255, 0, 175, 255): ('secondary', (28, 28, 32, 255)),
    (0, 255, 255, 255): ('tertiary', (150, 150, 150, 255)),
    (255, 0, 255, 255): ('quaternary', (20, 20, 20, 255)),
    (255, 175, 0, 255): ('headlight left', (255, 244, 190, 255)),
    (0, 255, 200, 255): ('headlight right', (255, 244, 190, 255)),
    (185, 255, 0, 255): ('taillight left', (120, 0, 0, 255)),
    (255, 60, 0, 255): ('taillight right', (120, 0, 0, 255)),
}


def material_role(color):
    return VEHICLE_MARKERS.get(tuple(color), ('material', tuple(color)))[0]


def preview_color(color, vehicle=True):
    color = tuple(color)
    if vehicle and color in VEHICLE_MARKERS:
        return VEHICLE_MARKERS[color][1]
    # RenderWare materials commonly use 204 as neutral texture modulation.
    if color[:3] == (204, 204, 204):
        return (255, 255, 255, color[3])
    return color


def component_visible(name, show_damaged=False):
    name = name.casefold()
    if name.endswith('_vlo'):
        return False
    if '_dam' in name or name.endswith('dam'):
        return show_damaged
    return True


def source_score(model, item, source_filter=None):
    """Prefer the model's named texture package, then common vehicle/effects data."""
    source = item.source
    stem = source.stem.casefold()
    model_stem = model.path.stem.casefold()
    score = 0
    if source_filter is not None and source == source_filter:
        score -= 1000
    if stem == model_stem:
        score -= 500
    if model.category == 'Cars':
        if stem == 'lr_cars':
            score -= 400
        elif stem in ('lr_effects', 'txd'):
            score -= 200
    if model.category == 'Skins' and stem in ('lr_skins', 'player', 'playerhi'):
        score -= 300
    if model.category in ('Buildings', 'Roads') and stem in ('lr_maps', 'gta3'):
        score -= 300
    if model.category == 'Interiors' and stem in ('lr_interiors', 'gta_int'):
        score -= 300
    if not any(entry.supported for _, entry in item.variants):
        score += 10000
    return score, str(source)
