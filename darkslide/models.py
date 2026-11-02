"""Built-in immutable firmware identities; never supplied by an imported project."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Model:
    id: str
    name: str
    firmware: str
    filename: str
    input_size: int
    input_sha: str
    payload_sha: str
    version: bytes
    project: int
    # Decoded payload length. GR III 2.10 / IIIx 1.60 share one layout, GR III 2.20 / IIIx 1.70 another.
    payload_size: int = 59718388

GR3 = Model('gr3-2.10-reviewed-1', 'GR III', '2.10', 'fwdc239b.bin', 30391780,
    'd6a3c9c288d784080e7b283222ecdd8752adfeed6faa36dab44f73115354bfd4',
    '53da690797f35ad6dc2c429d8ca1d0de8e333c211ed959816813982cd093b703', bytes.fromhex('020a151b'), 0x024c024c)
GR3X = Model('gr3x-1.60-reviewed-1', 'GR IIIx', '1.60', 'fwdc243b.bin', 30391788,
    '708e79f3b7b96a7e1581b4acf4c412233050a1832718ef7f06c8397db1b431c7',
    'c4bfc09d1148deec2155b21bd3d63be8085cf835cbe5e5cde4832747f519e630', bytes.fromhex('013c0b11'), 0x02610261)
# These originals use a rebuilt system segment. Addresses are mapped separately
# from the 2.10 / 1.60 layout; package identity is always checked before use.
GR3_220 = Model('gr3-2.20-reviewed-1', 'GR III', '2.20', 'fwdc239b.bin', 30472516,
    '5c53760b3b564c87239dbf7f5c94d0f6fd91accd156ec4ba672ebfd6b861793e',
    '5b475fcc434dec0f12e7956b02b5b38959d599b7a4ff90486263c46816a21b6d', bytes.fromhex('0214141c'), 0x024c024c,
    payload_size=59798532)
GR3X_170 = Model('gr3x-1.70-reviewed-1', 'GR IIIx', '1.70', 'fwdc243b.bin', 30472528,
    'aa8e3ae1349d94e83be381c20530e41d5e57b75c27ef24ba81c7b017d6c11592',
    '1129b9289b3cc68ab6f407a87d60db750483cc5ae41a6609596f6fe676b14521', bytes.fromhex('01461412'), 0x02610261,
    payload_size=59798532)
NEW_220 = (GR3_220, GR3X_170)

def get(identifier=None):
    if identifier is None or identifier == GR3.id:
        return GR3
    if identifier == GR3X.id:
        return GR3X
    for model in NEW_220:
        if identifier == model.id:
            return model
    raise ValueError('Unknown built-in firmware identity')

def for_template(template):
    if type(template) is not dict:
        raise ValueError('Invalid template')
    model = get(template.get('id'))
    if (template.get('model'), template.get('firmware')) != (model.name, model.firmware):
        raise ValueError('Template/model mismatch')
    return model

def layout(model):
    from .fwread import V210, V220
    return dict(V220 if model in NEW_220 else V210, payload_sha256=model.payload_sha)


def is_220(model):
    """GR III 2.20 or GR IIIx 1.70 (the 2026-10-21 firmware generation)."""
    return model in NEW_220


def is_iiix(model):
    """GR IIIx 1.60 or 1.70."""
    return model in (GR3X, GR3X_170)


def film_base(model):
    """The 2.10 / 1.60 model whose film data, base model and fitting serve this one. 2.20 / 1.70 hold
    the same factory curves, look matrices and Standard base (checked 2026-10-26), at relocated addresses."""
    return {GR3_220: GR3, GR3X_170: GR3X}.get(model, model)


ALL = (GR3, GR3X, GR3_220, GR3X_170)

def from_sha(digest):
    for model in ALL:
        if model.input_sha == digest:
            return model
    raise ValueError("需要已支持机型的官方原件")


def for_project(project):
    model = from_sha(project.get("input_sha256"))
    if project.get("template_id") != model.id:
        raise ValueError("工程模板与官方原件家族不一致")
    return model


TEMPLATE_FILES = {GR3.id: "gr3_v210.json", GR3X.id: "gr3x_v160.json",
                  GR3_220.id: "gr3_v220.json", GR3X_170.id: "gr3x_v170.json"}


def template_for(model):
    from . import template
    return template.load(TEMPLATE_FILES[model.id])


# 1.0 (user, 2026-10-16): the IIIx photo-recipe module grows the firmware and only
# serves in-camera re-development of old RAW, so 1.0 does not offer it. Code kept.
IIIX_RECIPE_MODULE = False


def capabilities(model):
    if model in NEW_220:
        # 2.20 / 1.70: everything 2.10 / 1.60 has. GR III 2.20 uses the full-firmware generator with the
        # relocated v29 film writer; GR IIIx 1.70 uses the IIIx candidate route like 1.60. Fitting runs the
        # relocated emulator (same Standard base as 2.10 / 1.60); 柔光 is the optimized core at the relocated
        # addresses; the shutdown image covers seven routes (20th / 30th anniversary added).
        from .inplace_generation import production_available
        gr3 = model == GR3_220
        return dict(import_ready=True, sparse_prepare=True, full_firmware=gr3,
                    film_output=gr3 and production_available() is True,
                    recipe_extension=False, candidate_generation=not gr3, candidate_recipe_extension=False,
                    camera_fit=True, photo_recipe_read=False, menu_order=True, menu_order_write=True,
                    soft_glow=True, wide_ratio=False, date_stamp=False, shutdown=True, films=True, new_firmware=True,
                    model=model.name, firmware=model.firmware, filename=model.filename,
                    note="%s %s 已全部适配：预设图标、名称、颗粒、菜单排序、胶片、LUT 拟合、柔光、关机画面（离线核对，机身未实测）" % (model.name, model.firmware))
    from .inplace_generation import production_available
    film_enabled = model == GR3 and production_available() is True
    return dict(import_ready=True, sparse_prepare=True, full_firmware=model == GR3,
                film_output=film_enabled, recipe_extension=False, candidate_generation=model == GR3X,
                candidate_recipe_extension=model == GR3X and IIIX_RECIPE_MODULE, camera_fit=True,
                photo_recipe_read=model == GR3X and IIIX_RECIPE_MODULE,
                # Image Control menu order: both series sort it and write it into the camera (IIIx 1.60 holds
                # the seven tables at the GR III 2.10 addresses with the same bytes; checked 2026-10-25).
                menu_order=model in (GR3, GR3X), menu_order_write=model in (GR3, GR3X),
                # 柔光 and the wide ratios (whole-camera switches, see camera_features): 柔光 v22.6 is written
                # for both series; 16:9 / 65:24 too, untested on a GR IIIx (HDF bodies are refused at generation).
                soft_glow=model in (GR3, GR3X), wide_ratio=model in (GR3, GR3X), date_stamp=False, shutdown=True, films=True, new_firmware=False,
                model=model.name, firmware=model.firmware, filename=model.filename,
                note=(("支持 IIIx 完整候选生成；DNG 自动配方扩展可选；离线验证，机身待测" if IIIX_RECIPE_MODULE else
                       "支持 IIIx 完整候选生成（原位替换，不扩容）；离线验证，机身待测") if model == GR3X else
                      ("GR III 不扩容胶片生成已开放；具体机身实测状态见型号说明" if film_enabled else "胶片完整输出尚未开放")))
