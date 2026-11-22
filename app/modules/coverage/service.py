from app.modules.coverage.sampling import choose_slots

NON_VERIFIED = ('PENDING', 'ISOLATED')
ISOLATED_REASONS = {'OBSERVATION_CONFLICT', 'PHOTO_REUSED', 'PHOTO_ID_CONFLICT'}


def evidence_state(binding, decision):
    if decision['plan'] is None:
        return 'ISOLATED', decision['reason']
    if binding is None:
        return 'PENDING', 'MISSING_PHOTO'
    if binding['reason'] is not None:
        return ('ISOLATED' if binding['reason'] in ISOLATED_REASONS else 'PENDING'), binding['reason']
    return 'VERIFIED', None


def coverage(intake, plans, photos, target):
    selected, strata = choose_slots(intake, target)
    plan_index = {(item['store'], item['slot']): item for item in plans['decisions']}
    binding_index = {(item['visit'], item['slot']): item for item in photos['bindings']}
    slots = []
    for entry in selected:
        key = (entry['store'], entry['slot'])
        decision = plan_index[key]
        visit = decision['visit']
        binding = binding_index.get((visit['visit'], entry['slot'])) if visit else None
        state, reason = evidence_state(binding, decision)
        slots.append({'key': entry['key'], 'store': entry['store'], 'slot': entry['slot'], 'group': entry['group'],
                      'category': entry['category'], 'visit': visit, 'plan': decision['plan'],
                      'plan_candidates': decision['candidates'], 'evidence_status': state, 'reason': reason,
                      'evidence': binding, 'score': entry['score'], 'source': entry['source']})
    rows, summary = [], {'verified': 0, 'pending': 0, 'isolated': 0}
    for stratum in strata:
        members = [row for row in slots if row['group'] == stratum['code']]
        counts = {'COMPLIANT': 0, 'VIOLATION': 0, 'PENDING': 0, 'ISOLATED': 0}
        verified = 0
        for row in members:
            if row['evidence_status'] == 'VERIFIED':
                verified += 1
            else:
                counts[row['evidence_status']] += 1
                summary[row['evidence_status'].lower()] += 1
        unfilled = stratum['target'] - len(members)
        rows.append({**{key: stratum[key] for key in ('code', 'title', 'weight', 'pool', 'target', 'ranked_candidates')},
                     'selected': len(members), 'verified': verified, 'pending': counts['PENDING'],
                     'isolated': counts['ISOLATED'], 'unfilled': unfilled,
                     'gap': stratum['target'] - verified})
        summary['verified'] += verified
    return {'target': target, 'selected': len(slots), 'unfilled': target - len(slots), **summary,
            'strata': rows, 'slots': slots}
