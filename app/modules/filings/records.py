from app.modules.intake.clock import utc_timestamp
from app.modules.intake.records import load_unique
from app.modules.intake.values import InvalidRow, identifier, integer, sku


def filings_from(table, store_map, slots, plan_ids, aliases):
    """Parse HQ substitute filings.

    A filing is bound to exactly one store, one shelf and one plan version; rows
    pointing anywhere else are quarantined instead of being re-scoped by guess.
    """
    def convert(row):
        store = identifier(row['store'])
        if store not in store_map:
            raise InvalidRow('UNKNOWN_STORE')
        slot = identifier(row['slot'])
        if (store, slot) not in slots:
            raise InvalidRow('UNKNOWN_SLOT')
        plan = identifier(row['plan'])
        if plan not in plan_ids:
            raise InvalidRow('UNKNOWN_PLAN')
        start, end = (utc_timestamp(row[key]) for key in ('start_utc', 'end_utc'))
        if start >= end:
            raise InvalidRow('BAD_INTERVAL')
        name = sku(row['sku'])
        return {'filing': identifier(row['filing']), 'store': store, 'slot': slot, 'plan': plan,
                'sku': aliases.get(name, name), 'facings': integer(row['facings'], 1, 999),
                'price_cents': integer(row['price_cents'], 1, 10_000_000),
                'start_utc': start.isoformat(), 'end_utc': end.isoformat()}

    # A filing whose copies disagree is never silently picked: every copy is isolated.
    rows, issues = load_unique(table, convert, ('filing',))
    return rows, issues
