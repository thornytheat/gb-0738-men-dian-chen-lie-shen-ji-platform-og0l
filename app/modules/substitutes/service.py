from app.modules.intake.clock import utc_timestamp
from app.modules.substitutes.records import filing_index, filing_versions

# 只有「缺品」和「乱陈」属于缺货顶柜语义；排面、价签问题不能用备案抵消
OFFSETTABLE = {'MISSING_SKU', 'WRONG_SKU'}


def register(intake, plans):
    rows, issues = filing_versions(intake['tables']['substitutes'], intake['stores'],
                                   intake['slots'], plans['aliases'])
    return {'rows': rows, 'index': filing_index(rows), 'quarantine': issues}


def _base(status, candidates, substitute_sku=None, on_shelf=False):
    return {'status': status, 'eligible': False, 'filing': None, 'candidates': candidates,
            'substitute_sku': substitute_sku, 'on_shelf': on_shelf,
            'sources': [row['source']['id'] for row in candidates]}


def evaluate_slot(decision, evidence, registry, visit_utc):
    """逐柜给出备案裁决。返回 None 表示本柜没有任何与应陈商品相关的备案可追溯。"""
    plan = decision['plan']
    if plan is None:
        return None
    store, slot, expected = decision['store'], decision['slot'], plan['sku']
    related = registry['index'].get(expected, [])
    if not related:
        return None
    scoped = [row for row in related if (row['store'], row['slot']) == (store, slot)]
    if not scoped:
        cross_store = any(row['store'] != store for row in related)
        return _base('FILING_SCOPE_STORE' if cross_store else 'FILING_SCOPE_SLOT', related)
    stamp = utc_timestamp(visit_utc)
    active = [row for row in scoped if utc_timestamp(row['start_utc']) <= stamp < utc_timestamp(row['end_utc'])]
    if not active:
        return _base('FILING_NOT_ACTIVE', scoped)
    if len({(row['filing'], row['version']) for row in active}) > 1:
        return _base('FILING_OVERLAP', active)
    chosen = sorted(active, key=lambda row: (row['filing'], row['version'], row['source']['line']))[0]
    products = evidence['products'] if evidence else {}
    if chosen['substitute_sku'] not in products:
        result = _base('FILING_NOT_PRESENT', active, chosen['substitute_sku'], False)
        result['filing'] = chosen
        return result
    observed = products[chosen['substitute_sku']]
    return {'status': 'FILING_OK', 'eligible': True, 'filing': chosen, 'candidates': active,
            'substitute_sku': chosen['substitute_sku'], 'on_shelf': True, 'observed': observed,
            'sources': [chosen['source']['id']]}


def clearances(coverage, registry):
    """对全部抽中柜位产出逐柜备案裁决，并在运行内强制同一备案只能抵扣一次。"""
    decisions, used = [], {}
    for entry in coverage['slots']:
        visit_utc = entry['visit']['at_utc'] if entry['visit'] else None
        evaluation = evaluate_slot(entry, entry['evidence'], registry, visit_utc) if visit_utc else None
        if evaluation is None:
            decisions.append({'key': entry['key'], 'status': 'NO_FILING', 'eligible': False,
                              'filing': None, 'candidates': [], 'substitute_sku': None,
                              'on_shelf': False, 'sources': []})
            continue
        if evaluation['eligible']:
            identity = (evaluation['filing']['filing'], evaluation['filing']['version'])
            if identity in used:
                evaluation = {**evaluation, 'eligible': False, 'status': 'FILING_DUPLICATE_USE',
                              'reused_by': used[identity],
                              'sources': [*evaluation['sources'], used[identity]]}
            else:
                used[identity] = entry['key']
        decisions.append({'key': entry['key'], **evaluation})
    by_key = {item['key']: item for item in decisions}
    return {'rows': decisions, 'by_key': by_key, 'quarantine': registry['quarantine'],
            'filing_rows': registry['rows']}


def substitute_clearance(intake, plans, coverage):
    # coverage['slots'] 已按店组权重与哈希排名确定顺序，重复抵扣时先到先得，结果与输入行序无关
    return clearances(coverage, register(intake, plans))
