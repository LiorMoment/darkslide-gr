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

GR3 = Model('gr3-2.10-reviewed-1', 'GR III', '2.10', 'fwdc239b.bin', 30391780,
    'd6a3c9c288d784080e7b283222ecdd8752adfeed6faa36dab44f73115354bfd4',
    '53da690797f35ad6dc2c429d8ca1d0de8e333c211ed959816813982cd093b703', bytes.fromhex('020a151b'), 0x024c024c)
GR3X = Model('gr3x-1.60-reviewed-1', 'GR IIIx', '1.60', 'fwdc243b.bin', 30391788,
    '708e79f3b7b96a7e1581b4acf4c412233050a1832718ef7f06c8397db1b431c7',
    'c4bfc09d1148deec2155b21bd3d63be8085cf835cbe5e5cde4832747f519e630', bytes.fromhex('013c0b11'), 0x02610261)

def get(identifier=None):
    if identifier is None or identifier == GR3.id:
        return GR3
    if identifier == GR3X.id:
        return GR3X
    raise ValueError('Unknown built-in firmware identity')

def for_template(template):
    if type(template) is not dict:
        raise ValueError('Invalid template')
    model = get(template.get('id'))
    if (template.get('model'), template.get('firmware')) != (model.name, model.firmware):
        raise ValueError('Template/model mismatch')
    return model

def layout(model):
    from .fwread import V210
    return dict(V210, payload_sha256=model.payload_sha)


ALL = (GR3, GR3X)

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


def template_for(model):
    from . import template
    return template.load("gr3x_v160.json" if model == GR3X else "gr3_v210.json")


# 1.0 (user, 2026-10-16): the IIIx photo-recipe module grows the firmware and only
# serves in-camera re-development of old RAW, so 1.0 does not offer it. Code kept.
IIIX_RECIPE_MODULE = False


def capabilities(model):
    from .inplace_generation import production_available
    film_enabled = model == GR3 and production_available() is True
    return dict(import_ready=True, sparse_prepare=True, full_firmware=model == GR3,
                film_output=film_enabled, recipe_extension=False, candidate_generation=model == GR3X,
                candidate_recipe_extension=model == GR3X and IIIX_RECIPE_MODULE, camera_fit=True,
                photo_recipe_read=model == GR3X and IIIX_RECIPE_MODULE,
                # Image Control menu order: both series sort it and write it into the camera (IIIx 1.60 holds
                # the seven tables at the GR III 2.10 addresses with the same bytes; checked 2026-10-25).
                menu_order=model in (GR3, GR3X), menu_order_write=model in (GR3, GR3X),
                # 柔光 and the 65:24 wide ratio (whole-camera switches, see camera_features): 柔光 v22.6 is
                # written for both series (2026-10-25); 65:24 is not built yet, so it cannot be on.
                soft_glow=model in (GR3, GR3X), wide_ratio=False,
                model=model.name, firmware=model.firmware, filename=model.filename,
                note=(("支持 IIIx 完整候选生成；DNG 自动配方扩展可选；离线验证，机身待测" if IIIX_RECIPE_MODULE else
                       "支持 IIIx 完整候选生成（原位替换，不扩容）；离线验证，机身待测") if model == GR3X else
                      ("GR III 不扩容胶片生成已开放；具体机身实测状态见型号说明" if film_enabled else "胶片完整输出尚未开放")))
