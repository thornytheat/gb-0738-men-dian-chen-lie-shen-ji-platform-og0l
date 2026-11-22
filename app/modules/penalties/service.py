from collections import Counter

from app.modules.penalties.rules import candidates
from app.modules.substitutes.service import OFFSETTABLE


def judge(coverage, campaign, clearances):
    rows, counts = [], Counter()
    for entry in coverage['slots']:
        state = entry['evidence_status']
        matched = candidates(entry['plan'], entry['evidence'], campaign['penalty_cents']) if state == 'VERIFIED' else []
        if state == 'VERIFIED':
            status = 'VIOLATION' if matched else 'COMPLIANT'
        else:
            status = state
        applied = matched[0] if matched else None
        reason = applied['code'] if applied else entry['reason']
        clearance = clearances['by_key'][entry['key']]
        gross = applied['cents'] if applied else 0
        offset_cents, offset_rule = 0, None
        effective = clearance
        if applied is not None and clearance['eligible']:
            if applied['code'] in OFFSETTABLE:
                offset_cents, offset_rule = gross, applied['code']
            else:
                # 备案本身有效，但本柜罚的是排面/价签，不能抵；只在罚则行留痕，不改备案原裁决
                effective = {**clearance, 'eligible': False, 'status': 'FILING_RULE_NOT_OFFSETTABLE'}
        net = gross - offset_cents
        rows.append({'key': entry['key'], 'store': entry['store'], 'slot': entry['slot'], 'group': entry['group'],
                     'category': entry['category'], 'status': status, 'reason': reason, 'candidates': matched,
                     'applied': applied, 'original_cents': gross, 'offset_cents': offset_cents,
                     'offset_rule': offset_rule, 'net_cents': net, 'clearance': effective,
                     'visit': entry['visit'], 'plan': entry['plan'], 'plan_candidates': entry['plan_candidates'],
                     'evidence': entry['evidence'], 'roster_source': entry['source']})
        counts[status] += 1
    return {'rows': rows, 'counts': {key: counts[key] for key in ('COMPLIANT', 'VIOLATION', 'PENDING', 'ISOLATED')}}
