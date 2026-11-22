from collections import defaultdict
from hashlib import sha256


def apportion(groups, target):
    """Largest-remainder allocation; ties fall back to ascending group code."""
    total = sum(group['weight'] for group in groups)
    exact = {group['code']: target * group['weight'] / total for group in groups}
    quotas = {code: int(value) for code, value in exact.items()}
    remainder = target - sum(quotas.values())
    order = sorted(groups, key=lambda group: (-(exact[group['code']] - int(exact[group['code']])), group['code']))
    for group in order[:remainder]:
        quotas[group['code']] += 1
    return quotas


def score_for(seed, store, slot):
    return sha256(seed.encode() + b'\x00' + store.encode() + b'\x00' + slot.encode()).hexdigest()


def rank_slots(intake):
    seed = intake['campaign']['seed']
    group_of = {row['store']: row['group'] for row in intake['stores']}
    ranked = defaultdict(list)
    for row in intake['slots']:
        group = group_of[row['store']]
        ranked[group].append({'key': f"{row['store']}:{row['slot']}", 'store': row['store'], 'slot': row['slot'],
                              'category': row['category'], 'group': group, 'source': row['source'],
                              'score': score_for(seed, row['store'], row['slot'])})
    for entries in ranked.values():
        entries.sort(key=lambda entry: (entry['score'], entry['key']))
    return ranked


def choose_slots(intake, target):
    ranked = rank_slots(intake)
    quotas = apportion(intake['campaign']['groups'], target)
    chosen, strata = [], []
    for group in intake['campaign']['groups']:
        pool = ranked.get(group['code'], [])
        quota = quotas[group['code']]
        picked = pool[:quota]
        chosen.extend(picked)
        strata.append({'code': group['code'], 'title': group['title'], 'weight': group['weight'],
                       'target': quota, 'pool': len(pool), 'selected': len(picked), 'unfilled': quota - len(picked),
                       'ranked_candidates': pool})
    return chosen, strata
