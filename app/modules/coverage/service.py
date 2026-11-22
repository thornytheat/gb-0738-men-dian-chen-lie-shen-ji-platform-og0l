from collections import Counter

from app.modules.coverage.sampling import choose_slots

ISOLATING = ('PHOTO_ID_CONFLICT', 'PHOTO_REUSED', 'OBSERVATION_CONFLICT', 'BAD_OBSERVATION')


def evaluate(decision, binding):
    visit, plan = decision['visit'], decision['plan']
    if visit is None:
        return 'PENDING', 'VISIT_MISSING'
    if plan is None:
        return 'ISOLATED', decision['reason']
    if binding is None:
        return 'PENDING', 'MISSING_PHOTO'
    reason = binding['reason']
    if reason in ISOLATING:
        return 'ISOLATED', reason
    if reason:
        return 'PENDING', reason
    return 'VERIFIED', None


def coverage(intake, plans, photos, target):
    chosen, strata = choose_slots(intake, target)
    decisions = {(row['store'], row['slot']): row for row in plans['decisions']}
    bindings = {(row['visit'], row['slot']): row for row in photos['bindings']}
    rows = []
    for slot in chosen:
        decision = decisions[slot['store'], slot['slot']]
        visit = decision['visit']
        binding = bindings.get((visit['visit'], slot['slot'])) if visit else None
        status, reason = evaluate(decision, binding)
        evidence = binding or {'photos': [], 'products': {}, 'completed_sources': [],
                               'conflicting_skus': [], 'photo_issues': []}
        rows.append({**slot, 'visit': visit, 'plan': decision['plan'], 'plan_reason': decision['reason'],
                     'plan_candidates': decision['candidates'],
                     'evidence': evidence, 'evidence_status': status, 'reason': reason})
    counts = Counter(row['evidence_status'] for row in rows)
    results = []
    for group in strata:
        group_rows = [row for row in rows if row['group'] == group['code']]
        local = Counter(row['evidence_status'] for row in group_rows)
        results.append({**group, 'verified': local['VERIFIED'], 'pending': local['PENDING'],
                        'isolated': local['ISOLATED'], 'gap': group['target'] - local['VERIFIED']})
    return {'target': target, 'selected': len(rows), 'unfilled': sum(row['unfilled'] for row in strata),
            'verified': counts['VERIFIED'], 'counts': {key: counts[key] for key in ('VERIFIED', 'PENDING', 'ISOLATED')},
            'strata': results, 'slots': rows}
