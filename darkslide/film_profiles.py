"""Strict camera parameter profiles, separate from approximate preview LUTs."""
import json
import math
from pathlib import Path
from .film_numeric import validate_matrix_q13,validate_film_curves,FilmNumericError
from .film_requests import FilmRequestError

PROFILE_ROOT=Path(__file__).resolve().parent.parent/"film_curves"/"camera"

def validate_profile(profile,ident,model_id=None):
    from . import models
    model=models.get(model_id)
    if type(profile) is not dict or profile.get("schema")!=1 or type(profile.get("schema")) is not int or profile.get("id")!=ident:
        raise FilmRequestError("相机胶片数据格式或编号不匹配")
    if profile.get("model")!=model.name or profile.get("firmware")!=model.firmware:
        raise FilmRequestError("胶片数据不适用于 "+model.name+" "+model.firmware)
    if models.is_iiix(model) and ident.startswith("user_lut_"):
        if profile.get("official_sha256")!=model.input_sha or profile.get("base_model_id")!="gr3x-160-upstream-neutral-stubbed-v2":
            raise FilmRequestError("IIIx 用户拟合参数缺少匹配的基础模型身份")
    try:
        matrix=validate_matrix_q13(profile.get("look_matrix_q13"))
        curves=validate_film_curves(profile.get("curves_u14"))
    except FilmNumericError as error:
        raise FilmRequestError(str(error)) from error
    post=profile.get("post_matrix_q512")
    if type(post) is not list or len(post)!=3:
        raise FilmRequestError("曲线后矩阵格式错误")
    for row in post:
        if type(row) is not list or len(row)!=3 or any(type(v) is not int or not -32768<=v<=32767 for v in row) or sum(row)!=512:
            raise FilmRequestError("曲线后矩阵必须为白保持的 Q512")
    # The hook sign-extends each input to 19 bits, then uses signed A32
    # MUL/MLA and adds 256 before >>9. Bound every column's partial sums.
    if any(sum(abs(post[row][column]) for row in range(3))>8191 for column in range(3)):
        raise FilmRequestError("曲线后矩阵超出安全的 32 位累加范围")
    return dict(profile,look_matrix_q13=[list(row) for row in matrix],
                curves_u14=[list(row) for row in curves],post_matrix_q512=[list(row) for row in post])

def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise FilmRequestError("胶片数据有重复字段")
        result[key]=value
    return result

def load_profile(ident,model_id=None):
    from .film_requests import validate_request
    validate_request({"kind":"film","slot":8,"film":ident})
    path=PROFILE_ROOT/(ident+".json")
    if path.resolve().parent!=PROFILE_ROOT.resolve():
        raise FilmRequestError("胶片数据不在相机数据目录内")
    if not path.is_file():raise FilmRequestError("这卷胶片的相机数据尚未就绪："+ident)
    def invalid(value):raise FilmRequestError("胶片数据包含非有限数值")
    def finite(value):
        result=float(value)
        if not math.isfinite(result):invalid(value)
        return result
    try:profile=json.loads(path.read_text(encoding="utf-8"),object_pairs_hook=_pairs,parse_constant=invalid,parse_float=finite)
    except (OSError,UnicodeError,json.JSONDecodeError) as error:raise FilmRequestError("无法读取相机胶片数据") from error
    validated=validate_profile(profile,ident)
    from . import models
    model=models.get(model_id)
    if model==models.GR3X:
        validated=dict(validated,model=model.name,firmware=model.firmware,
            compatibility=dict(source_model="GR III",source_firmware="2.10",target=model.id,
                evidence="IIIx 1.60 layout and synthetic neutral base comparison",
                native_effect_verified=False))
    elif models.is_220(model):
        # Same film data: 2.20 / 1.70 hold the 2.10 / 1.60 factory curves, matrices and Standard base.
        validated=dict(validated,model=model.name,firmware=model.firmware,
            compatibility=dict(source_model="GR III",source_firmware="2.10",target=model.id,
                evidence="2.20 / 1.70 factory curves, look matrices and emulated Standard base equal 2.10 / 1.60",
                native_effect_verified=False))
    return validate_profile(validated,ident,model.id)

def ready_profile_ids():
    """Data readiness is separate from firmware export availability."""
    result=[]
    for path in sorted(PROFILE_ROOT.glob("*.json")):
        try:load_profile(path.stem)
        except FilmRequestError:continue
        result.append(path.stem)
    return result


def data_revision():
    """Identity of the loaded camera catalog; filenames alone do not name data."""
    import hashlib
    h = hashlib.sha256()
    for path in sorted(PROFILE_ROOT.glob('*.json')):
        h.update(path.name.encode('utf-8'))
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()
