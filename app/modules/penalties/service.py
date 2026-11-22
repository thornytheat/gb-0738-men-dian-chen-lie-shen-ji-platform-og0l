from collections import Counter

from app.modules.filings.review import review_shelf
from app.modules.penalties.rules import candidates

STATUSES = ('COMPLIANT', 'VIOLATION', 'PENDING', 'ISOLATED')


def excuse(matched, filing, products):
    """Drop only what an approved on-shelf substitute legitimately replaces.

    The substitute answers a missing required SKU and is not itself an
    out-of-plan product. Extra goods, short facings and price gaps stay.
    """
    if filing['verdict'] != 'APPLIED':
        return matched
    kept = []
    for finding in matched:
        if finding['code'] == 'MISSING_SKU':
            continue
        if finding['code'] == 'WRONG_SKU':
            others = [code for code in finding['proof']['skus'] if code != filing['filing_sku']]
            if not others:
                continue
            finding = {**finding, 'proof': {**finding['proof'], 'skus': others,
                                            'sources': [line for code in others for line in products[code]['sources']]}}
        kept.append(finding)
    return kept


def judge(coverage, campaign, filings=None):
    filings = filings or {'present': False, 'rows': [], 'quarantine': [], 'scopes': {}}
    rows, counts = [], Counter()
    for entry in coverage['slots']:
        state = entry['evidence_status']
        products = (entry.get('evidence') or {}).get('products') or {}
        matched = candidates(entry['plan'], entry['evidence'], campaign['penalty_cents']) if state == 'VERIFIED' else []
        filing = review_shelf(entry, filings)
        assessed = matched[0] if matched else None
        effective = excuse(matched, filing, products) if matched else []
        executed = effective[0] if effective else None
        assessed_cents = assessed['cents'] if assessed else 0
        executed_cents = executed['cents'] if executed else 0
        offset = assessed_cents - executed_cents
        if state == 'VERIFIED':
            status = 'VIOLATION' if executed else 'COMPLIANT'
        else:
            status = state
        if executed:
            # A filed shelf that still fails on a missing SKU must say why the filing did not count.
            reason = executed['code']
            if reason == 'MISSING_SKU' and filing['verdict'] not in ('NOT_FILED', 'NOT_REQUIRED'):
                reason = filing['reason']
        elif offset:
            reason = 'FILING_APPLIED'
        else:
            reason = entry['reason']
        rows.append({'key': entry['key'], 'store': entry['store'], 'slot': entry['slot'], 'group': entry['group'],
                     'category': entry['category'], 'status': status, 'reason': reason,
                     'candidates': matched, 'applied': executed,
                     'assessed_cents': assessed_cents, 'offset_cents': offset, 'original_cents': executed_cents,
                     'filing': filing,
                     'visit': entry['visit'], 'plan': entry['plan'], 'plan_candidates': entry['plan_candidates'],
                     'evidence': entry['evidence'], 'roster_source': entry['source']})
        counts[status] += 1
    return {'rows': rows, 'counts': {key: counts[key] for key in STATUSES},
            'filing_present': filings.get('present', False),
            'filing_rows': len(filings.get('rows', [])),
            'filing_quarantine': filings.get('quarantine', [])}
