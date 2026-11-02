"""Compose reviewed edits on one official payload and pack exactly once."""
from . import edit_core as builder, a_shutdown, grain_menu, film_requests, fwaddr, menu_order, models, soft_glow, wide_ratio

class CompositeError(ValueError):
    pass

def _merge_delta(before, merged, declared, ranges, changed, changes):
    """Validate each delta completely before merging any of its spans."""
    builder._require(type(changed) is bytes and len(changed) == len(before), 'delta size')
    builder._require(type(changes) is list, 'delta declarations')
    pending = []
    for item in changes:
        builder._require(type(item) is dict and 'offset' in item and 'length' in item,
                         'delta record')
        a, n = item['offset'], item['length']
        builder._require(type(a) is int and type(n) is int and n > 0, 'typed delta span')
        b = a + n
        builder._require(0 <= a < b <= len(before)-4, 'delta bounds/checksum ownership')
        builder._require(not any(a < y and b > x for x, y in ranges + pending),
                         'overlapping feature edits')
        pending.append((a, b))
    previous = 0
    for a, b in sorted(pending) + [(len(before), len(before))]:
        for at in range(previous, a, 65536):
            end = min(a, at + 65536)
            builder._require(changed[at:end] == before[at:end], 'undeclared delta bytes')
        previous = b
    for item, (a, b) in zip(changes, pending):
        merged[a:b] = changed[a:b]
        ranges.append((a, b))
        declared.append(dict(item))

def build_composite(official, edits, template, jpeg=None, *, film_profiles=None, film_layout=None):
    return _build(official, edits, template, jpeg, film_profiles=film_profiles,
                  film_layout=film_layout, inplace=False)


def build_inplace_composite(official, edits, template, jpeg=None, *, film_profiles=None):
    """Internal complete implementation; not exposed by the HTTP output gate."""
    from .inplace_generation import layout_for
    requests = film_requests.validate_batch([e for e in edits if e.get('kind') == 'film'])
    return _build(official, edits, template, jpeg, film_profiles=film_profiles,
                  film_layout=layout_for(requests, models.for_template(template)), inplace=True)


def _build(official, edits, template, jpeg=None, *, film_profiles=None, film_layout=None, inplace=False):
    try:
        builder._template(template)
        model = models.get(template.get("id"))
        builder._require(not inplace or model in (models.GR3, models.GR3_220), "GR III inplace compositor only")
        # Icons, names and grain (24) plus five films; the menu order and 柔光 or 画幅 (at most one each) come on top.
        builder._require(type(edits) is list and (bool(edits) or jpeg is not None), 'no requested changes')
        builder._require(len(edits) <= 31, 'too many edits')
        regular, grains, films, menus, glows, wides = [], [], [], [], [], []
        for edit in edits:
            builder._require(type(edit) is dict, 'edit object')
            if edit.get('kind') == 'film':
                films.append(film_requests.validate_request(edit))
            elif edit.get('kind') == 'grain_menu':
                grain_menu.validate_request(edit)
                grains.append(edit)
            elif edit.get('kind') == 'menu_order':
                menus.append(menu_order.request_from_edit(edit))
            elif edit.get('kind') == 'soft_glow':
                soft_glow.validate_request(edit)
                glows.append(edit)
            elif edit.get('kind') == 'wide_ratio':
                wide_ratio.validate_request(edit)
                wides.append(edit)
            else:
                regular.append(edit)
        builder._require(len(menus) <= 1, 'duplicate menu order edit')
        builder._require(len(glows) <= 1, 'duplicate soft glow edit')
        builder._require(len(wides) <= 1 and not (wides and glows), 'wide ratio carries soft glow itself')
        builder._require(not wides or model in (models.GR3, models.GR3X), '画幅只支持 GR III 2.10 和 GR IIIx 1.60')
        builder._require(len(edits) - len(menus) - len(glows) - len(wides) <= 29, 'too many edits')
        builder._require(not glows or model in models.ALL, '柔光只支持 GR III 2.10 / 2.20 和 GR IIIx 1.60 / 1.70')
        # GR III 2.20 films use the relocated v29 writer (fwaddr); GR IIIx 1.70 films go through gr3x_generation.
        builder._require(not models.is_220(model) or not films or (inplace and model == models.GR3_220),
                         '胶片还没有适配 %s %s' % (model.name, model.firmware))
        # The seven menu tables are only known for the GR III 2.10 package (ordinary and HDF bodies).
        builder._require(not menus or menu_order.supported(template), menu_order.UNSUPPORTED)
        builder._require(len(regular)+len(grains)<=24, 'too many regular edits')
        film_requests.validate_batch(films)
        if films and not inplace and not film_requests.available():
            raise film_requests.FilmRequestError("胶片写入尚未开启")
        builder._require(len(grains) <= 1, 'duplicate grain menu edit')
        if regular:
            builder._validate_edits(regular, template)
        builder._require(type(official) is bytes and len(official) == (builder.INPUT_SIZE if model == models.GR3 else model.input_size) and
                         builder._sha(official) == (builder.INPUT_SHA if model == models.GR3 else model.input_sha), 'fixed official input required')
        before, frames, stop, footer = builder._decode(official, model.id)
        builder._require(builder._sha(before) == (builder.PAYLOAD_SHA if model == models.GR3 else model.payload_sha), 'official payload identity')
        merged, declared, ranges = bytearray(before), [], []
        if regular:
            changed, changes = builder.apply_edits(before, regular, template)
            _merge_delta(before, merged, declared, ranges, changed, changes)
        if grains:
            changed, changes = grain_menu.apply(before) if model == models.GR3 else grain_menu.apply(before, model.id)
            _merge_delta(before, merged, declared, ranges, changed, changes)
        if menus:
            # Seven tables, 78 bytes; the checksum word stays with packing.
            changed, changes = menu_order.apply(before, menus[0], template)
            _merge_delta(before, merged, declared, ranges, changed, changes)
        if glows:
            # 柔光 v22.28: fixed windows; any overlap with another feature is refused by _merge_delta.
            changed, changes = soft_glow.apply(before, model.id)
            _merge_delta(before, merged, declared, ranges, changed, changes)
        if wides:
            # Fixed windows; any overlap with another feature is refused by _merge_delta.
            changed, changes = wide_ratio.apply(before, model.id, glow=wides[0]['glow'], date=wides[0].get('date', False))
            _merge_delta(before, merged, declared, ranges, changed, changes)
        if films:
            builder._require(model in (models.GR3, models.GR3_220), "IIIx films require dedicated checked adapter")
            layout = film_layout or film_requests.FILM_LAYOUT
            builder._require(layout in ('legacy_arena_v1', 'four_curve_v1', 'inplace_history_cache_exit_v29', 'inplace_history_cache_exit_vivid5_v1', 'inplace_history_cache_exit_vivid5_220_v1'), 'film layout')
            if layout in ('inplace_history_cache_exit_v29', 'inplace_history_cache_exit_vivid5_v1', 'inplace_history_cache_exit_vivid5_220_v1'):
                from .inplace_generation import layout_for
                builder._require(layout == layout_for(films, model), 'film selection/layout mismatch')
                from . import film_inplace_apply as film_writer
            elif layout == 'four_curve_v1':
                from . import film_four_apply as film_writer
            else:
                from . import film_writer
            with fwaddr.firmware(model):
                if film_profiles is None:
                    changed, changes = film_writer.apply(before,films)
                else:
                    changed, changes = film_writer.apply(before,films,profiles=film_profiles)
            _merge_delta(before,merged,declared,ranges,changed,changes)
        shutdown = None
        if jpeg is not None:
            changed, shutdown = a_shutdown.apply_a_shutdown(before, jpeg) if model == models.GR3 else a_shutdown.apply_a_shutdown(before, jpeg, model.id)
            _merge_delta(before, merged, declared, ranges, changed, shutdown['edits'])
        builder._require(declared, 'no actual payload changes')
        result = builder._pack_payload(official, before, frames, stop, footer, bytes(merged), declared) if model == models.GR3 else builder._pack_payload(official, before, frames, stop, footer, bytes(merged), declared, model.id)
        result.update(film_layout=film_layout if films else None, decoded_growth_bytes=0,
                      features=sorted({e['kind'] for e in edits} | ({'shutdown'} if jpeg is not None else set())),
                      shutdown=shutdown, compositor='Darkslide one-payload compositor v04 (negative grain, menu order, soft glow)',
                      candidate_file_written=False, camera_verified=False, exportable=False,
                      device_installation_verified=False,
                      independent_entropy_decode_required=jpeg is not None)
        if shutdown is not None:
            result.update(shared_capacity=shutdown['capacity'],
                          request_ids=shutdown['request_ids'], shared_path=shutdown['shared_path'])
        return result
    except (builder.BuildError, a_shutdown.AShutdownError, film_requests.FilmRequestError,
            menu_order.MenuOrderError) as exc:
        raise CompositeError(str(exc)) from exc
