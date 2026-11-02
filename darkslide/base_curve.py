"""Runtime-only read contract for an explicitly modelled stock Standard base.

No vendor firmware arrays in the repository; no firmware patch/export or device
access. Values are computed by original-code emulation under synthetic defaults,
not a measurement of a camera's white-balance/exposure state.
"""
import copy
import hashlib
from pathlib import Path
import threading
import subprocess
import sys
import os
import json
import importlib.util
from . import fwaddr, fwread, models
from ._vendor.gr3_base_emulator import OriginalEmulator,DEFAULTS,ZERO_STUBS

MODEL_ID="gr3-210-upstream-neutral-stubbed-v2"
SETTINGS_MODEL_ID="gr3-210-upstream-four-sliders-stubbed-v1"
SLIDER_OFFSETS=dict(key=0x77f,contrast=0x780,highlight=0x781,shadow=0x782)
_CACHE={}
_LOCK=threading.Lock()


class BaseCurveError(ValueError):pass


def _settings(settings):
    if settings is None:return None
    if type(settings) is not dict or any(type(k) is not str or k not in SLIDER_OFFSETS for k in settings):
        raise BaseCurveError("影调设置只接受key/contrast/highlight/shadow")
    if any(type(v) is not int or not -4<=v<=4 for v in settings.values()):
        raise BaseCurveError("机身影调设置须为-4到4的整数")
    return {k:settings.get(k,0) for k in SLIDER_OFFSETS}


def _model(sha, settings=None):
    try: model = models.from_sha(sha)
    except ValueError as exc: raise BaseCurveError(str(exc)) from exc
    if models.is_iiix(model) and settings is not None:
        raise BaseCurveError("IIIx 当前只核对中性基础模型")
    return model


def _model_id(settings, model=models.GR3):
    if models.is_iiix(model):return "gr3x-160-upstream-neutral-stubbed-v2"
    return MODEL_ID if settings is None else SETTINGS_MODEL_ID


def _settings_metadata(settings):
    result=dict(tone=11,stage=1,slider_neutral_encoding=4)
    if settings is not None:
        result.update(slider_values=dict(settings),slider_encodings={k:v+4 for k,v in settings.items()})
    return result


def _backend_identity(origin, library):
    origin=Path(origin).resolve();library=Path(library).resolve()
    package=origin.parent
    digest=hashlib.sha256()
    for source in sorted(package.rglob("*.py")):
        digest.update(str(source.relative_to(package)).encode());digest.update(source.read_bytes())
    return dict(package_path=str(origin),package_sha256=digest.hexdigest(),
                library_path=str(library),library_sha256=hashlib.sha256(library.read_bytes()).hexdigest())


def _backend_library(origin):
    # Names used by the approved Unicorn 2.1.4 bindings on both Mac CPU families
    # and Windows. Download availability is separate from the local self-check.
    name={"darwin":"libunicorn.2.dylib","win32":"unicorn.dll"}.get(sys.platform)
    if name is None:raise BaseCurveError("拟合组件只支持 Mac 和 Windows")
    pattern="*unicorn*.dylib" if sys.platform=="darwin" else "*unicorn*.dll"
    libraries=sorted(Path(origin).resolve().parent.rglob(pattern))
    if len(libraries)!=1 or libraries[0].name!=name or not libraries[0].is_file():
        raise BaseCurveError("尚未核对这个 Unicorn 平台后端")
    return libraries[0].resolve()


def _same(value,expected):
    if type(value) is not type(expected):return False
    if type(expected) is dict:return value.keys()==expected.keys() and all(_same(value[k],v) for k,v in expected.items())
    if type(expected) is list:return len(value)==len(expected) and all(_same(a,b) for a,b in zip(value,expected))
    return value==expected


def _validate_result(result,sha,backend,settings=None):
    settings=_settings(settings)
    if type(result) is not dict:raise BaseCurveError("只读仿真回执不是数据对象")
    model=_model(sha,settings)
    fixed=dict(model=model.name,firmware=model.firmware,official_sha256=sha,model_id=_model_id(settings,model),
        emulator_version="2.1.4",backend=backend,value_range=[0,16384],output_normalization_divisor=16384,
        payload_sha256=model.payload_sha,settings=_settings_metadata(settings))
    if type(result.get("schema")) is not int or result["schema"]!=1 or any(not _same(result.get(k),v) for k,v in fixed.items()):
        raise BaseCurveError("只读仿真数据身份不匹配")
    for field,value in dict(emulated_original_arm_instructions=True,physical_target_cpu_executed=False,
                           native_effect_verified=False,full_modified_firmware_created=False,location_verified=True).items():
        if result.get(field) is not value:raise BaseCurveError("只读仿真范围声明不匹配")
    values=result.get("values")
    if type(values) is not list or len(values)!=256 or any(type(v) is not int or not 0<=v<=16384 for v in values):
        raise BaseCurveError("只读仿真输出范围不匹配")
    assumptions=result.get("assumptions")
    if type(assumptions) is not dict or type(assumptions.get("base_write_coverage")) is not int or assumptions["base_write_coverage"]!=512 or assumptions.get("original_guest_rom_read_only") is not True:
        raise BaseCurveError("只读仿真生成证据不匹配")


def _compute(raw,sha,expected_backend,settings=None):
    settings=_settings(settings)
    try:
        import unicorn
    except ImportError as error:raise BaseCurveError("只读基础曲线需要已批准的 Unicorn 仿真后端") from error
    from unicorn.unicorn_py3 import unicorn as binding
    backend=_backend_identity(unicorn.__file__,binding.uclib._name)
    if backend!=expected_backend:raise BaseCurveError("实际加载的仿真后端与核对对象不一致")
    if unicorn.__version__!="2.1.4":raise BaseCurveError("尚未核对这个 Unicorn 版本")
    model=_model(sha,settings)
    try:
        decoded=fwread.unpack(raw)
        payload=fwread.Payload(decoded,models.layout(model))
        # GR III 2.20 / IIIx 1.70: the same code at relocated addresses (fwaddr), longer RTOS.
        with fwaddr.firmware(model):
            rtos=bytes(payload.p[0x10870:0x10870+fwaddr.layout()["rtos_size"]])
            emu=OriginalEmulator(rtos);values=emu.standard_base(
                None if settings is None else {SLIDER_OFFSETS[k]:v+4 for k,v in settings.items()})
    except (ValueError,RuntimeError) as error:raise BaseCurveError("原件只读仿真未完成："+str(error)) from error
    if len(values)!=256 or any(type(v) is not int or not 0<=v<=16384 for v in values):
        raise BaseCurveError("基础曲线输出范围或长度不匹配")
    result=dict(schema=1,model=model.name,firmware=model.firmware,official_sha256=sha,
        emulator_version=unicorn.__version__,backend=backend,
        payload_sha256=hashlib.sha256(decoded).hexdigest(),model_id=_model_id(settings,model),
        method=("original ARM code, synthetic neutral defaults, declared service stubs" if settings is None
                else "original ARM code, synthetic defaults and requested sliders, declared service stubs"),
        values=list(values),value_range=[0,16384],location_verified=True,
        location_verification="pinned original; gamma=obj+0x17c at runtime, bounded synthetic allocation",
        settings=_settings_metadata(settings),
        assumptions=dict(synthetic_zero_ram=True,stub_addresses=[hex(v) for v in sorted(ZERO_STUBS)],
            observed_stub_calls={hex(k):v for k,v in emu.stub_calls.items()},svc_return_zero=list(emu.services),
            observed_generation_frames=emu.frames,
            base_write_coverage=emu.base_write_coverage,gamma_object_identity=emu.gamma_identity,original_guest_rom_read_only=True,
            white_balance_runtime_state_measured=False,physical_pixel_model_verified=False),
        input_domain=dict(points="0..255 table index; physical linear-sRGB relation is an assumption",
            mid_gray_index=7.8,mid_gray_index_verified=False,linear_scale_assumed=7.8/0.18),
        composition=dict(base_then_film=True,film_index="min(base/64,255)",
            film_interpolation="firmware6-bit integer lerp, truncate negative differences toward zero",
            endpoint="base>=16320 uses film sample255; base endpoint16384 is permitted",
            base_input_interpolation="fitter uses linear interpolation of base table indices; physical domain unverified"),
        output_normalization_divisor=16384,
        output_space="display-encoded RGB interpreted as sRGB by the upstream fit model; not ICC conversion verification",
        upstream_default_comparison=dict(same_initialization_contract=True,numeric_identity_verified=False,
            reason="foreign extracted base_gamma_std.npy is not imported; no numeric difference claimed"),
        emulated_original_arm_instructions=True,physical_target_cpu_executed=False,
        native_effect_verified=False,full_modified_firmware_created=False)
    _validate_result(result,sha,backend,settings)
    return result


def base_curve(official_bin_path,settings=None):
    """Read Standard-model Base for named -4..4 sliders, isolated from App.

    Missing keys mean zero. This is not a physical pixel or other-preset model.
    """
    settings=_settings(settings)
    try:path=Path(official_bin_path).resolve();raw=path.read_bytes()
    except (OSError,TypeError) as error:raise BaseCurveError("无法读取用户官方原件") from error
    sha=hashlib.sha256(raw).hexdigest()
    model=_model(sha,settings)
    spec=importlib.util.find_spec("unicorn")
    if spec is None or not spec.origin:raise BaseCurveError("只读基础曲线需要已批准的 Unicorn 仿真后端")
    origin=Path(spec.origin).resolve()
    library=_backend_library(origin)
    backend=_backend_identity(origin,library)
    local=hashlib.sha256()
    for source in (Path(__file__),Path(fwread.__file__),Path(models.__file__),Path(__file__).with_name("_vendor")/"gr3_base_emulator.py",
                   Path(__file__).with_name("fwaddr.py"),Path(__file__).with_name("data")/"reloc_220.json"):
        local.update(source.read_bytes())
    key=(sha,_model_id(settings,model),local.hexdigest(),json.dumps(backend,sort_keys=True),
         json.dumps(settings,sort_keys=True))
    with _LOCK:
        if key in _CACHE:return copy.deepcopy(_CACHE[key])
        env=dict(os.environ)
        app=str(Path(__file__).resolve().parent.parent)
        env.pop("PYTHONPATH",None)
        env["LIBUNICORN_PATH"]=str(library.parent)
        # -I excludes cwd, user site and inherited Python import settings.
        bootstrap="import sys,runpy;sys.path[:0]=sys.argv[1:3];sys.argv=sys.argv[3:];runpy.run_module('darkslide.base_curve',run_name='__main__')"
        try:
            process=subprocess.run([sys.executable,"-I","-B","-X","utf8"]+["-O"]*sys.flags.optimize+["-c",bootstrap,app,str(origin.parent.parent),"darkslide.base_curve","--worker",str(path),json.dumps(backend)]+([] if settings is None else [json.dumps(settings,sort_keys=True)]),
                                   capture_output=True,text=True,encoding="utf-8",env=env,timeout=60)
        except subprocess.TimeoutExpired as error:raise BaseCurveError("只读仿真超过60秒限制") from error
        if process.returncode!=0:
            raise BaseCurveError("只读仿真子进程未完成（退出码%s）：%s"%(process.returncode,process.stderr[-300:]))
        try:result=json.loads(process.stdout)
        except json.JSONDecodeError as error:raise BaseCurveError("只读仿真回执无效") from error
        _validate_result(result,sha,backend,settings)
        _CACHE[key]=copy.deepcopy(result)
        return copy.deepcopy(result)


if __name__=="__main__":
    if len(sys.argv) not in (4,5) or sys.argv[1]!="--worker":raise SystemExit("internal readonly worker")
    raw=Path(sys.argv[2]).read_bytes();sha=hashlib.sha256(raw).hexdigest()
    print(json.dumps(_compute(raw,sha,json.loads(sys.argv[3]),None if len(sys.argv)==4 else json.loads(sys.argv[4])),ensure_ascii=False))
