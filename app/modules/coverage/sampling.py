from hashlib import sha256


def apportion(groups, target):
    """最大余数法按店组权重分配目标柜数；余数并列按店组编号排序。"""
    weights = [(group['code'], group['weight']) for group in groups]
    total_weight = sum(weight for _, weight in weights)
    quotas = {code: target * weight // total_weight for code, weight in weights}
    remainder = target - sum(quotas.values())
    ranked = sorted(weights, key=lambda item: (-(target * item[1] % total_weight), item[0]))
    for code, _ in ranked[:remainder]:
        quotas[code] += 1
    return {group['code']: quotas[group['code']] for group in groups}


def rank_score(seed, store, slot):
    digest = sha256(f'{seed}\0{store}\0{slot}'.encode()).hexdigest()
    return digest, int(digest[:16], 16)


def choose_slots(intake, target):
    """组内按 SHA-256(种子 NUL 门店 NUL 柜位) 确定性排序抽取，与输入顺序无关。"""
    seed = intake['campaign']['seed']
    groups = intake['campaign']['groups']
    store_group = {store['store']: store['group'] for store in intake['stores']}
    pools = {group['code']: [] for group in groups}
    for slot in intake['slots']:
        score, number = rank_score(seed, slot['store'], slot['slot'])
        pools[store_group[slot['store']]].append({**slot, 'group': store_group[slot['store']],
                                                  'key': f"{slot['store']}:{slot['slot']}", 'score': score,
                                                  'rank_number': number, 'source': slot['source']})
    quotas = apportion(groups, target)
    chosen, strata = [], []
    for group in groups:
        ranked = sorted(pools[group['code']], key=lambda item: (item['rank_number'], item['key']))
        quota = quotas[group['code']]
        selected = ranked[:quota]
        chosen.extend(selected)
        strata.append({'code': group['code'], 'title': group['title'], 'weight': group['weight'],
                       'pool': len(ranked), 'target': quota, 'selected': len(selected),
                       'unfilled': quota - len(selected), 'ranked_candidates': ranked})
    return chosen, strata
