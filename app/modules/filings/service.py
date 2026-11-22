from collections import defaultdict

from app.modules.filings.records import filings_from


def load_filings(intake, plans):
    table = intake['tables'].get('filings')
    if table is None:
        return {'present': False, 'rows': [], 'quarantine': [], 'scopes': {},
                'table': None, 'counts': {}}
    stores = {row['store'] for row in intake['stores']}
    slots = {(row['store'], row['slot']) for row in intake['slots']}
    plan_sources = {row['plan']: row['source']['id'] for row in plans['plans']}
    rows, issues = filings_from(table, stores, slots, set(plan_sources), plans['aliases'])
    for row in rows:
        # 每条备案都带所依据陈列图版本的源行，财务可回查当时依据。
        row['plan_source'] = plan_sources[row['plan']]
    scopes = defaultdict(list)
    for row in rows:
        scopes[f"{row['store']}:{row['slot']}"].append(row)
    for entries in scopes.values():
        entries.sort(key=lambda row: (row['filing'], row['source']['line']))
    return {'present': True, 'rows': rows, 'quarantine': issues, 'scopes': dict(scopes),
            'table': table, 'aliases': plans['aliases']}
