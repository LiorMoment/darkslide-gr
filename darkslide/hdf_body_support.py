"""Read-only capability reporting; never change firmware or CollaboMode."""
from .hdf_guard import BODIES,need
MODES={0:2,1:3,2:11,3:10,4:12}
# GR III 2.20 / GR IIIx 1.70: CollaboMode 5 / 6 select requests 13 / 14 (20th / 30th anniversary).
MODES_220={**MODES,5:13,6:14}

def shutdown_coverage(body,request_ids,observed_mode=None,modes=MODES):
    MODES=modes
    need(type(body) is str and body in BODIES,'explicit supported body required')
    need(type(request_ids) is tuple and all(type(x) is int and x in MODES.values() for x in request_ids),'request ids')
    need(len(request_ids)==len(set(request_ids)),'duplicate request')
    need(observed_mode is None or type(observed_mode) is int and observed_mode in MODES,'invalid observed mode')
    missing=sorted(set(MODES.values())-set(request_ids))
    request=None if observed_mode is None else MODES[observed_mode]
    status=('all_factory_routes_covered' if not missing else 'mode_evidence_required') if request is None else ('selected_route_covered' if request in request_ids else 'selected_route_not_covered')
    return {'body_declared':body,'collabo_mode_observed':observed_mode,'selected_request':request,'status':status,
            'missing_requests':missing,'body_setting_inferred':False,'camera_verified':False,
            'compatibility_approved':False,'note':'Observed mode must come from separate device evidence; package/body name cannot supply it.'}
