from app.modules.intake.clock import utc_timestamp
from app.modules.intake.records import load_unique
from app.modules.intake.values import InvalidRow, identifier, sku
from app.reasons import REASONS


def filing_versions(table, stores, slots, aliases):
    store_codes = {row['store'] for row in stores}
    slot_codes = {(row['store'], row['slot']) for row in slots}

    def convert(row):
        filing, version = identifier(row['filing']), identifier(row['version'])
        store, slot = identifier(row['store']), identifier(row['slot'])
        if store not in store_codes:
            raise InvalidRow('UNKNOWN_STORE')
        if (store, slot) not in slot_codes:
            raise InvalidRow('UNKNOWN_SLOT')
        expected, substitute = sku(row['expected_sku']), sku(row['substitute_sku'])
        expected, substitute = aliases.get(expected, expected), aliases.get(substitute, substitute)
        if expected == substitute:
            raise InvalidRow('FILING_SAME_SKU')
        start, end = (utc_timestamp(row[key]) for key in ('start_utc', 'end_utc'))
        if start >= end:
            raise InvalidRow('FILING_BAD_INTERVAL')
        return {'filing': filing, 'version': version, 'store': store, 'slot': slot,
                'expected_sku': expected, 'substitute_sku': substitute,
                'start_utc': start.isoformat(), 'end_utc': end.isoformat()}

    rows, issues = load_unique(table, convert, ('filing', 'version', 'store', 'slot'))
    # 同编号同版本同柜位但事实矛盾由 load_unique 标为 KEY_CONFLICT；备案语义下统一成版本冲突，便于裁决层识别
    for issue in issues:
        if issue['reason'] == 'KEY_CONFLICT':
            issue['reason'] = 'FILING_VERSION_CONFLICT'
            issue['label'] = REASONS['FILING_VERSION_CONFLICT']
    return rows, issues


def filing_index(rows):
    """按应陈商品归集全部备案版本，作用域判定交给逐柜裁决。"""
    grouped = {}
    for row in rows:
        grouped.setdefault(row['expected_sku'], []).append(row)
    return grouped
