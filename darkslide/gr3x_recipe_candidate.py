"""In-memory merged IIIx candidate; no downloadable build, file or device I/O."""
import hashlib
import struct
from . import models, package, compositor, composite_verify, film_writer
from . import gr3x_recipe_merge, gr3x_recipe_verify
from . import fwread, photo_recipe


def build(official, edits, template, *, profiles=None, experimental_recipe=False, shutdown_jpeg=None):
    if experimental_recipe is not True or models.for_template(template)!=models.GR3X:
        raise ValueError('explicit IIIx experimental recipe build required')
    if type(edits) is not list or any(type(e) is not dict for e in edits):
        raise ValueError('typed edits required')
    requested=[e for e in edits if e.get('kind')=='film']
    if any(e.get('slot') == 2 for e in requested):
        raise ValueError('鲜艳胶片暂不支持照片配方扩展，请使用不扩容生成')
    ordinary=[e for e in edits if e.get('kind')!='film']
    resolved=film_writer.resolve_profiles(requested,profiles,models.GR3X.id)
    source=package.Package.read(official,models.GR3X.id)
    merge=gr3x_recipe_merge.prepare(source.image,requested,resolved,shutdown_hdf=shutdown_jpeg is not None)
    if ordinary or shutdown_jpeg is not None:
        base=compositor.build_composite(official,ordinary,template,shutdown_jpeg)
        base_checks=composite_verify.verify(official,base,ordinary,template,shutdown_jpeg)
        ordinary_wire=base['output']
    else:
        ordinary_wire=official;base_checks=[dict(id='official_ordinary_base',ok=True)]
    if not base_checks or not all(c['ok'] for c in base_checks):raise ValueError('ordinary composite failed')
    image=bytearray(package.Package.read(ordinary_wire,models.GR3X.id).image)
    def pack(payload):
        payload=package.close_words(bytes(payload[:-4]))
        frames=package.raw_frames(payload)+b'\0\0'
        footer=struct.pack('<5I',source.trailer.project,source.trailer.sentinel,source.trailer.operation,len(frames),len(payload))
        return package.close_words(source.header+frames+bytes((-len(frames))%4)+source.auxiliary+footer)
    p=fwread.Payload(source.image,models.layout(models.GR3X))
    for w in merge['curve']['windows']:
        a=p.off(w.address)
        if p.off(w.address+len(w.expected)-1)!=a+len(w.expected)-1:raise ValueError('curve mapping')
        if image[a:a+len(w.expected)]!=w.expected:raise ValueError('ordinary/curve conflict')
        image[a:a+len(w.replacement)]=w.replacement
    curve_wire=pack(image)
    ext=merge['extension'];known={w.address:w.replacement for w in merge['curve']['windows'] if w.address in merge['overlap']}
    for w in ext['windows']:
        at=0x10870+w.address-0x53000000
        if image[at:at+len(w.expected)]!=known.get(w.address,w.expected):raise ValueError('merged hook conflict')
        image[at:at+len(w.replacement)]=w.replacement
    end=0x10870+0x10b55c0;image[end:end]=ext['extension']
    struct.pack_into('<I',image,0x1086c,ext['new_rtos_size'])
    wire=pack(image)
    checks=gr3x_recipe_verify.verify(official,wire,curve_wire,ordinary_wire,merge['curve']['windows'],
        {e['slot']:e['film'] for e in requested},resolved,merge['packets'],ext['native'],shutdown_hdf=shutdown_jpeg is not None)
    if not checks or not all(c['ok'] for c in checks):raise ValueError('merged readback failed: '+repr(checks))
    return dict(output=wire,output_sha256=hashlib.sha256(wire).hexdigest(),layout=merge['layout'],
        extension_sha256=hashlib.sha256(ext['extension']).hexdigest(),experimental_recipe=True,
        container=dict(encoded_bytes=struct.unpack_from('<I',wire,len(wire)-12)[0],
                       decoded_bytes=len(image),format='stored frames; unchanged original header/auxiliary'),
        checks=checks,base_checks=base_checks,overlap_addresses=[hex(a) for a in merge['overlap']],
        snapshot_numeric_sha256={s:photo_recipe.decode(b)['numeric_sha256'] for s,b in merge['packets'].items()},
        curve_growth_bytes=0,recipe_extension_bytes=len(ext['extension']),
        compression_errors=merge['curve'].get('compression_errors',{}),
        exportable=False,product_download_enabled=False,physical_camera_tested=False,
        native_source_sha256=ext['native']['source_sha256'],native_code_sha256=ext['native']['code_sha256'])
