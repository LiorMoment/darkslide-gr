"""Freeze IIIx desktop grading sources before fitting or replacing a LUT entry.

This records the selected source and user adjustments, not measured pixel
authorship. Numeric output is still supplied by the existing fitter. Notices
travel once at the top level; nested derivations retain their source identities.
"""
import copy
import hashlib
from . import film_luts, film_profiles, models, photo_recipe


def capture(store, pid, film, model=models.GR3X):
    """model: the project's IIIx model; built-in film data and the frozen packet are for that model."""
    uploaded_notices = {}
    if film is None:
        return dict(source=None, data_notices={})
    if photo_recipe.RESTORED_ID.fullmatch(film):
        profile = store.photo_recipe_profile(pid, film)
    elif film_luts.USER_ID.fullmatch(film):
        entry = store.film_luts(pid)[film]
        if entry.get('profile') is None:
            raw = store.film_lut_path(pid, film).read_bytes()
            film_luts.parse_cube(raw)
            if film_luts.lut_id(raw) != film or hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise ValueError('调色源 LUT 内容与编号不一致')
            # User-supplied comments may contain attribution. Do not infer a
            # licence or silently truncate it; packet capacity is checked below.
            headers = '\n'.join(line for line in raw.decode('utf-8-sig').splitlines()
                                if line.lstrip().startswith('#'))
            if entry.get('bake') is not None:
                derivation, notices = metadata(entry['bake'])
                return dict(source=dict(id=film, source_sha256=entry['sha256'], name=entry['name'],
                                        recipe_derivation=derivation), data_notices=notices)
            return dict(source=dict(id=film, source_sha256=entry['sha256'], name=entry['name']),
                        data_notices={'SOURCE_LUT_COMMENTS.txt': headers} if headers else {})
        profile = store.film_lut_profiles(pid, [film])[film]
        if entry.get('bake') is None:
            # Older fits did not carry uploaded comments in their profile.
            # Re-read the already hash-validated original, not today's catalog.
            raw = store.film_lut_path(pid, film).read_bytes()
            if hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise ValueError('调色源 LUT 在读取期间改变')
            headers = '\n'.join(line for line in raw.decode('utf-8-sig').splitlines()
                                if line.lstrip().startswith('#'))
            if headers:
                uploaded_notices['SOURCE_LUT_'+entry['sha256']+'.txt'] = headers
    else:
        profile = film_profiles.load_profile(film, model.id)
    # The portable encoder supplies built-in attribution from the data package,
    # but never replaces notices already frozen in a historical photo/profile.
    value = photo_recipe.decode(photo_recipe.encode(profile, 8, model.id), model.id)
    profile = value['profile']
    source = {k: copy.deepcopy(profile[k]) for k in
              ('id', 'provenance', 'photo_snapshot', 'recipe_derivation',
               'derived_from_photo_recipe') if k in profile}
    # Older profiles duplicated notices inside this legacy field. Keep their
    # source facts without multiplying the licence on every subsequent grade.
    if isinstance(source.get('derived_from_photo_recipe'), dict):
        source['derived_from_photo_recipe'].pop('data_notices', None)
    source['numeric_sha256'] = value['numeric_sha256']
    notices = copy.deepcopy(profile.get('data_notices', {}))
    for name, contents in uploaded_notices.items():
        if name in notices and notices[name] != contents:
            raise ValueError('调色源 LUT 注释与已存来源记录冲突')
        notices[name] = contents
    return dict(source=source, data_notices=notices)


def metadata(bake):
    """No retroactive attribution for older bakes without a frozen source."""
    from .store import Store
    if type(bake) is not dict or type(bake.get('recipe_source')) is not dict:
        raise ValueError('这份调色缺少原始来源记录，请重新点击调色的「拟合」')
    saved = bake['recipe_source']
    source, notices = saved.get('source'), saved.get('data_notices')
    if (set(saved) != {'source', 'data_notices'} or type(notices) is not dict or
            any(type(k) is not str or type(v) is not str for k, v in notices.items()) or
            (bake.get('film') is None and source is not None) or
            (bake.get('film') is not None and (type(source) is not dict or source.get('id') != bake['film']))):
        raise ValueError('调色来源记录与所选胶片不一致，请重新拟合')
    grade = Store._clean_grade('调色', bake.get('grade'))
    return dict(schema=1, source=copy.deepcopy(source), grade=grade,
                grade_scope='desktop request; numeric packet records the fitted camera result',
                camera_omitted_adjustments=[k for k in ('hH', 'hS', 'hL')
                    if any(grade.get(k, []))] + (['vib'] if grade.get('vib') else []),
                modified_by='LiorMoment / Darkslide'), copy.deepcopy(notices)


def attach(profile, bake, model=models.GR3X):
    derivation, notices = metadata(bake)
    result = copy.deepcopy(profile)
    result.update(recipe_derivation=derivation, data_notices=notices)
    # Reject oversize or unencodable provenance before declaring the fit ready.
    photo_recipe.encode(result, 8, model.id)
    return result


def verify(profile, bake, model=models.GR3X):
    derivation, notices = metadata(bake)
    if profile.get('recipe_derivation') != derivation or profile.get('data_notices') != notices:
        raise ValueError('调色拟合与来源记录不一致，请重新拟合')
    photo_recipe.encode(profile, 8, model.id)
