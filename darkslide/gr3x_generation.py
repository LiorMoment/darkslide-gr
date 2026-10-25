"""Explicit IIIx offline candidates, with independent readback and frozen sources.

The production capability remains closed. This adapter never changes the GR III
output gate. Recipe extensions are explicit and cannot pass the no-growth gate.
"""
import copy
import hashlib
import json
import struct
import time
from . import camera_features, models, fwread, package, package_inspect, compositor, composite_verify, rescue_gate
from . import edit_core, generate, film_requests, film_profiles, film_writer, body_target, menu_language
from . import gr3x_inplace, gr3x_inplace_integrity, gr3x_inplace_readback

LAYOUT = 'gr3x160_inplace_candidate_v1'
NOTE = 'GR IIIx 1.60 固件；标准版已有用户实机刷入使用，城市版、HDF 及刷回官方 1.60 未实机验证。'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _iiix_title(check):
    """Shared Chinese check titles, with the two GR III-specific wordings switched to IIIx 1.60."""
    title = generate.CHECK_TITLES.get(check['id'], check.get('title', check['id']))
    return title.replace('GR III 2.10', 'GR IIIx 1.60').replace('版本保持 2.10', '版本保持 1.60')


def ordinary(official, edits, template, jpeg):
    if edits or jpeg is not None:
        result = compositor.build_composite(official, edits, template, jpeg)
        checks = composite_verify.verify(official, result, edits, template, jpeg)
        if not checks or not all(c['ok'] is True for c in checks):
            raise generate.GenerateError('IIIx 普通组合独立核对失败', checks)
        return result['output'], checks
    return official, [dict(id='official_ordinary_base', ok=True, title='普通编辑基线为官方原件')]


def curve_candidate(official, edits, template, profiles, jpeg):
    requests = [e for e in edits if e['kind']=='film']
    plain, checks = ordinary(official,[e for e in edits if e['kind']!='film'],template,jpeg)
    if not requests:
        return plain, checks
    source = package.Package.read(official, models.GR3X.id)
    before = source.image
    resolved = film_writer.resolve_profiles(requests,profiles,models.GR3X.id)
    plan = gr3x_inplace.plan_offline(before,requests,resolved)
    changed = bytearray(fwread.unpack(plain))
    p = fwread.Payload(before,models.layout(models.GR3X))
    actual = []
    for w in plan['windows']:
        at=p.off(w.address);end=at+len(w.expected)
        if p.off(w.address+len(w.expected)-1)!=end-1 or changed[at:end]!=w.expected:
            raise generate.GenerateError('普通编辑与胶片写入位置冲突')
        changed[at:end]=w.replacement
        actual.append((at,end,w.address))
    base = package.Package.read(plain,models.GR3X.id)
    output, _, _, _ = package.pack_change(plain,base.image,
        base.frames, base.stream_end, base.trailer.values(),bytes(changed),
        [dict(offset=a,length=b-a,what='IIIx film') for a,b,_ in actual])
    # Independent fixed-mask coverage and numeric oracle, read from actual output.
    after=bytes(fwread.unpack(output));baseline=bytes(fwread.unpack(plain))
    from collections import namedtuple
    Window=namedtuple('ReadWindow','address expected replacement')
    windows=tuple(Window(address,before[a:b],after[a:b]) for a,b,address in actual)
    evidence=gr3x_inplace_integrity.verify(before,windows,{e['slot']:e['film'] for e in requests},resolved,layout=gr3x_inplace_readback.LAYOUT)
    restored=bytearray(after)
    for a,b,_ in actual:restored[a:b]=baseline[a:b]
    if (len(after)!=len(before) or restored[:-4]!=baseline[:-4] or
            any(evidence.get(k) is not True for k in ('data_verified','shared_bytes_match_reference','complete_window_coverage_verified'))):
        raise generate.GenerateError('IIIx 曲线完整输出读回不一致')
    checks += [dict(id='iiix_complete_curve_readback',title='曲线、矩阵和完整改动范围独立读回一致',ok=True,details=evidence)]
    return output,checks


def extension_body(official, output, body, shutdown_jpeg=None):
    """Check actual RTOS windows after a separately validated extension.

    Do not truncate a grown image to masquerade as a no-growth candidate. The
    protected windows are addressed in the unchanged original RTOS prefix.
    """
    from . import gr3x_recipe_gate, hdf_guard
    checks=gr3x_recipe_gate.gate(official,output)
    if not checks or not all(c['ok'] is True for c in checks):
        raise generate.GenerateError('配方扩展边界核对失败',checks)
    before,after=bytes(fwread.unpack(official)),bytes(fwread.unpack(output))
    report=body_target.build_report(before,body,before)
    rows=[]
    for window in report['protected_windows']:
        start,end=int(window['start'],16),int(window['end_exclusive'],16)
        if not (0x53000000<=start<end<=0x540b55c0):
            raise generate.GenerateError('保护窗不在原 RTOS 范围')
        rows.append(dict(window,unchanged=hdf_guard.window(before,start,end)==hdf_guard.window(after,start,end)))
    changed=[r['name'] for r in rows if not r['unchanged']]
    street=None
    if shutdown_jpeg is not None:
        from . import gr3x_street_read
        try:street=gr3x_street_read.verify(before,after,shutdown_jpeg)
        except (ValueError,TypeError,IndexError,struct.error) as exc:
            raise generate.GenerateError('Street 条件分流核对失败：'+str(exc)) from exc
        allowed=[r['name'] for r in rows if int(r['start'],16)==0x5319f180 and int(r['end_exclusive'],16)==0x5319f2bc]
        if len(allowed)!=1 or changed!=allowed:
            raise generate.GenerateError('Street 之外的保护窗改变')
    else:
        from . import photo_recipe_extension_read
        end=0x10870+struct.unpack_from('<I',after,0x1086c)[0]
        if photo_recipe_extension_read.inspect(after[0x10870+0x10b55c0:end])['shutdown_hdf']:
            raise generate.GenerateError('未提供关机图却启用 Street 标志')
    report.update(protected_windows=rows,changed_windows=changed,
        status='review_required' if changed else 'protected_windows_unchanged',candidate_payload_sha256=sha(after),
        extension_gate_verified=True)
    if street:
        report.update(status='protected_windows_with_verified_shutdown_hook',shutdown_conditional=street)
        checks.append(dict(id='iiix_street_conditional',ok=True,title='Street 分流指令、机身条件和关机图资源独立核对',details=street))
    return checks,report


def run(store, project, template, official, *, recipe_extension=False):
    if type(recipe_extension) is not bool:
        raise generate.GenerateError('配方扩展选项必须是布尔值')
    saved=copy.deepcopy(store.get_project(project['id']));template=copy.deepcopy(template)
    if saved!=project or saved.get('unsaved'):
        raise generate.GenerateError('工程未保存或已改变')
    if models.for_project(saved)!=models.GR3X or models.for_template(template)!=models.GR3X:
        raise generate.GenerateError('候选仅适用于 IIIx 1.60')
    pending = camera_features.refusal(saved.get('camera_features'))
    if pending:   # 柔光 / 65:24 are not in the firmware generation yet: refused, never left out
        raise generate.GenerateError(pending)
    from . import menu_order
    unreadable=saved.get('menu_order_unreadable') or (saved.get('draft') or {}).get('menu_order_unreadable')
    if unreadable:
        raise generate.GenerateError('已存的菜单顺序无法读取（%s），没有生成：请在「菜单顺序」里重新排好或恢复原厂顺序并保存' % unreadable)
    try:order=menu_order.normalize(saved.get('menu_order'))
    except menu_order.MenuOrderError as exc:raise generate.GenerateError('菜单顺序格式错误：%s' % exc) from exc
    features=saved.get('camera_features') or {}
    if not saved.get('changes') and order is None and not camera_features.edits(features) and not recipe_extension:
        raise generate.GenerateError('还没有改动')
    try:
        edit_core._template(template)
        body=body_target.require_family(body_target.of(saved),'GR IIIx','1.60')
        if body is None:raise generate.GenerateError('请先选择具体机身版本')
        source=store.input_path(models.GR3X.input_sha)
        if type(official) is not bytes or len(official)!=models.GR3X.input_size or sha(official)!=models.GR3X.input_sha or source.read_bytes()!=official:
            raise generate.GenerateError('IIIx 官方原件身份不一致')
        revision=film_profiles.data_revision()
        effective=store.film_changes_for_generate(saved['id'],saved.get('changes',{}))
        requests=film_requests.from_changes(effective)
        ids=sorted({e['film'] for e in requests if e['film'].startswith('user_lut_')})
        index=copy.deepcopy(store.film_luts(saved['id']))
        profiles=copy.deepcopy(store.film_lut_profiles(saved['id'],ids)) if ids else {}
        names={e['icon']['asset'] for k,e in effective.items() if k!='shutdown' and 'icon' in e}
        if 'shutdown' in effective:names.add(effective['shutdown']['asset'])
        assets={n:store.asset_path(saved['id'],n).read_bytes() for n in names}
        menu_lang=saved.get('menu_language')
        if menu_lang is not None:
            try:menu_language.validate(menu_lang)
            except menu_language.MenuLanguageError as exc:raise generate.GenerateError(str(exc))
        edits=generate.edits_from_changes(store,saved['id'],effective,template,include_films=True,asset_snapshot=assets,menu_lang=menu_lang,order=order,features=features)
        jpeg=assets[effective['shutdown']['asset']] if 'shutdown' in effective else None
        archive=None; extension={}; layout=LAYOUT
        if recipe_extension:
            from . import gr3x_recipe_candidate, gr3x_recipe_archive
            result=gr3x_recipe_candidate.build(official,edits,template,profiles=profiles,shutdown_jpeg=jpeg,experimental_recipe=True)
            output=result['output'];checks=result['base_checks']+result['checks']
            extra,report=extension_body(official,output,body,jpeg);checks+=extra
            archive=gr3x_recipe_archive.from_output(output)
            checks.append(gr3x_recipe_archive.verify(output,edits,archive))
            layout=gr3x_recipe_archive.LAYOUT
            extension={k:result[k] for k in ('recipe_extension_bytes','extension_sha256','native_source_sha256','native_code_sha256','snapshot_numeric_sha256','overlap_addresses')}
        else:
            output,checks=curve_candidate(official,edits,template,profiles,jpeg)
            old_footer, old_aux, _ = package_inspect.inspect(official, models.GR3X.id)
            new_footer, new_aux, _ = package_inspect.inspect(output, models.GR3X.id)
            payload = bytes(fwread.unpack(output))
            checks.append(dict(id='iiix_independent_container', title='独立读取文件边界、原头、附加段及双层校验和',
                ok=(old_footer[:3] == new_footer[:3] and old_footer[4] == new_footer[4] and old_aux == new_aux
                    and output[:128] == official[:128]
                    and sum(v[0] for v in struct.iter_unpack('<I', output)) & 0xffffffff == 0
                    and sum(v[0] for v in struct.iter_unpack('<I', payload)) & 0xffffffff == 0)))
            checks+=rescue_gate.gate(official,output,models.GR3X.id)
            report=body_target.build_report(bytes(fwread.unpack(official)),body,bytes(fwread.unpack(output)))
        checks.append(dict(id='body_protected_windows',title='IIIx 机身保护窗核对',ok=report['status'] in ('protected_windows_unchanged','protected_windows_with_verified_shutdown_hook'),detail=body_target.summary(report)))
        if not checks or not all(c.get('ok') is True for c in checks):
            raise generate.GenerateError('IIIx 候选独立核对未通过',checks)
        if (store.get_project(saved['id'])!=saved or source.read_bytes()!=official or
                store.film_changes_for_generate(saved['id'],saved.get('changes',{}))!=effective or
                store.film_luts(saved['id'])!=index or (ids and store.film_lut_profiles(saved['id'],ids)!=profiles) or
                film_profiles.data_revision()!=revision or
                any(store.asset_path(saved['id'],n).read_bytes()!=v for n,v in assets.items())):
            raise generate.GenerateError('生成期间工程、原件或配方来源改变')
        before,after=bytes(fwread.unpack(official)),bytes(fwread.unpack(output))
        manifest=dict(schema=1,project_id=saved['id'],project_title=saved['title'],project_version=len(saved.get('versions',[])),
            template_id=template['id'],model=models.GR3X.name,firmware=models.GR3X.firmware,
            created_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'),input_sha256=models.GR3X.input_sha,output_sha256=sha(output),
            output_filename=models.GR3X.filename,payload_sha256_before=sha(before),payload_sha256_after=sha(after),
            input_bytes=len(official),output_bytes=len(output),encoded_growth_bytes=len(output)-len(official),
            container_encoding="stored_frames" if recipe_extension else "preserve_original_frames_except_changes",
            decoded_growth_bytes=len(after)-len(before),curve_growth_bytes=0,firmware_layout=layout,
            checks=[dict(c,title=_iiix_title(c)) for c in checks],
            summary=generate.summarize(effective,template,include_films=True,order=order,features=features),menu_language=menu_lang,features=sorted({e['kind'] for e in edits}|({'photo_recipe'} if recipe_extension else set())|({'shutdown'} if jpeg else set())),
            experimental=True,production_approved=False,camera_verified=False,product_download_enabled=True,
            film_data_revision=revision,body=report,recipe_extension=extension,verification=NOTE,
            rescue=dict(official_file=models.GR3X.filename,official_sha256=models.GR3X.input_sha,how='IIIx 实机安装及同版本回刷待验证'))
        if jpeg is not None:
            manifest['body']['shutdown_routes']=body_target.shutdown_report(body)
            if report.get('shutdown_conditional'):
                manifest['body']['shutdown_routes']['conditional_request_3']=copy.deepcopy(report['shutdown_conditional'])
        if features.get('soft_glow'):
            manifest['verification']+=' '+generate.SOFT_GLOW_NOTE
        if order is not None:
            manifest['menu_order']=dict(order,video_policy=menu_order.VIDEO_POLICY)
            manifest['verification']+=' 影像控制菜单的新顺序还没在 GR IIIx 上实测：刷后请看拍照、视频、RAW 显影里的列表顺序，选中后拍出的风格和名称是否一致，关机重开和 U1–U3 里存的风格有没有变。'
        if archive is not None:
            manifest['film_recipe_archive']=archive
            manifest['summary'].append(dict(preset='照片配方',what='DNG / JPEG 保存、RAW 恢复',value='仅完整快照；旧 RAW 恢复后导出 JPEG 原样保存历史配方；机身待测'))
            manifest['verification']+=' 本包使用不压缩封装，文件约为原件的两倍；追加 RTOS 配方模块；DNG 保存实际生效值；JPEG 拍摄通过独立 APP15 分段保存当前完整配方。旧 RAW 须已有完整快照，恢复旧配方后导出 JPEG 时原样保存其历史包。内存或载体容量不足时保留照片、跳过配方，不以当前胶片冒充历史配方；选择另一胶片后按实际选择保存。'
        return output,manifest
    except generate.GenerateError:raise
    except (ValueError,OSError,KeyError,TypeError,struct.error) as exc:
        raise generate.GenerateError('IIIx 候选生成失败：'+str(exc)) from exc
