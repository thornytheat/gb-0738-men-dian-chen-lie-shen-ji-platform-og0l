from app.modules.intake.clock import utc_timestamp

VERDICTS = {
    'APPLIED': 'FILING_APPLIED',
    'SUBSTITUTE_ABSENT': 'SUBSTITUTE_ABSENT',
    'TERMS_NOT_MET': 'FILING_TERMS_NOT_MET',
    'UNVERIFIED': 'FILING_PENDING',
    'BLOCKED': 'FILING_BLOCKED',
    'PLAN_MISMATCH': 'FILING_PLAN_MISMATCH',
    'EXPIRED': 'FILING_EXPIRED',
    'NOT_REQUIRED': 'FILING_NOT_REQUIRED',
    'STORE_MISMATCH': 'FILING_STORE_MISMATCH',
    'SLOT_MISMATCH': 'FILING_SLOT_MISMATCH',
    'NOT_FILED': 'NO_FILING',
}


def _covers(filing, instant):
    return utc_timestamp(filing['start_utc']) <= utc_timestamp(instant) < utc_timestamp(filing['end_utc'])


def evaluate(entry, filing):
    """One shelf-scoped filing against one sampled shelf.

    Every gate has to pass before a filing may offset: same plan version, inside
    the filing window, shelf evidence verified, substitute actually observed on
    the shelf, and substitute facings/price meeting the filed terms.
    """
    plan = entry['plan']
    products = (entry.get('evidence') or {}).get('products') or {}
    substitute = products.get(filing['sku'])
    if plan is None:
        return 'BLOCKED'
    if filing['plan'] != plan['plan']:
        return 'PLAN_MISMATCH'
    if not _covers(filing, entry['visit']['at_utc']):
        return 'EXPIRED'
    if entry['evidence_status'] == 'ISOLATED':
        return 'BLOCKED'
    if entry['evidence_status'] != 'VERIFIED':
        return 'UNVERIFIED'
    if plan['sku'] in products:
        return 'NOT_REQUIRED'
    if substitute is None:
        return 'SUBSTITUTE_ABSENT'
    if substitute['facings'] < filing['facings'] or substitute['price_cents'] != filing['price_cents']:
        return 'TERMS_NOT_MET'
    return 'APPLIED'


def detail(entry, filing):
    products = (entry.get('evidence') or {}).get('products') or {}
    substitute = products.get(filing['sku'])
    plan = entry['plan']
    return {'filing': filing['filing'],
            'filing_store': filing['store'], 'filing_slot': filing['slot'],
            'filing_plan': filing['plan'], 'filing_plan_source': filing['plan_source'],
            'filing_source': filing['source']['id'], 'filing_sku': filing['sku'],
            'filing_facings': filing['facings'], 'filing_price_cents': filing['price_cents'],
            'window': {'start_utc': filing['start_utc'], 'end_utc': filing['end_utc']},
            'effective_plan': plan['plan'] if plan else None,
            'effective_plan_source': plan['source']['id'] if plan else None,
            'substitute_on_shelf': substitute is not None,
            'observed': None if substitute is None else {'sku': filing['sku'], 'facings': substitute['facings'],
                                                         'price_cents': substitute['price_cents'],
                                                         'sources': substitute['sources']}}


def _verdict(entry, filing):
    verdict = evaluate(entry, filing)
    return {'verdict': verdict, 'reason': VERDICTS[verdict], **detail(entry, filing)}


def review_shelf(entry, filings):
    """Per-shelf explanation: can a filing offset, on which plan version, and is the substitute on shelf."""
    store, slot = entry['store'], entry['slot']
    products = (entry.get('evidence') or {}).get('products') or {}
    plan = entry['plan']
    base = {'key': entry['key'], 'store': store, 'slot': slot,
            'effective_plan': plan['plan'] if plan else None,
            'effective_plan_source': plan['source']['id'] if plan else None,
            'visit_at_utc': entry['visit']['at_utc'] if entry['visit'] else None,
            'verdict': 'NOT_FILED', 'reason': VERDICTS['NOT_FILED'], 'filing': None,
            'filing_store': None, 'filing_slot': None, 'filing_plan': None, 'filing_plan_source': None,
            'filing_source': None, 'filing_sku': None, 'filing_facings': None, 'filing_price_cents': None,
            'substitute_on_shelf': False, 'observed': None, 'window': None, 'alternates': [], 'near_misses': []}
    in_scope = (filings.get('scopes') or {}).get(f"{store}:{slot}", [])
    if in_scope:
        assessed = [_verdict(entry, filing) for filing in in_scope]
        chosen = next((item for item in assessed if item['verdict'] == 'APPLIED'), assessed[0])
        base.update({key: chosen[key] for key in (
            'verdict', 'reason', 'filing', 'filing_store', 'filing_slot', 'filing_plan', 'filing_plan_source',
            'filing_source', 'filing_sku', 'filing_facings', 'filing_price_cents',
            'substitute_on_shelf', 'observed', 'window')})
        base['alternates'] = [item for item in assessed if item is not chosen]
        return base
    # Nothing is filed for this exact shelf. Show same-slot/other-store copies only as
    # near misses; they never offset because a filing's scope is store + shelf.
    needs_substitute = plan is None or plan['sku'] not in products
    observed = set(products)
    if not needs_substitute:
        return base
    near = []
    for filing in filings.get('rows', []):
        if filing['sku'] not in observed:
            continue
        item = _verdict(entry, filing)
        if filing['store'] == store:
            item.update(verdict='SLOT_MISMATCH', reason=VERDICTS['SLOT_MISMATCH'])
        else:
            item.update(verdict='STORE_MISMATCH', reason=VERDICTS['STORE_MISMATCH'])
        near.append(item)
    if near:
        near.sort(key=lambda item: (item['verdict'] != 'SLOT_MISMATCH', item['filing']))
        base.update({key: near[0][key] for key in (
            'verdict', 'reason', 'filing', 'filing_store', 'filing_slot', 'filing_plan', 'filing_plan_source',
            'filing_source', 'filing_sku', 'filing_facings', 'filing_price_cents',
            'substitute_on_shelf', 'observed', 'window')})
        base['near_misses'] = near
    return base
