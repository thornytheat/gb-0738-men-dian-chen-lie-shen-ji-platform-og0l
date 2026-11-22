import shutil

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from app.config import Options, Settings
from app.pipeline import Audit
from app.modules.settlement.service import run_audit
from app.store import Store
from conftest import appeal_payload, locate_or_fail


@pytest.fixture
def substitute_run(client):
    return client.post('/api/settlement/runs', json={'dataset': 'substitute'}).json()


@pytest.fixture
def scratch_client(settings, tmp_path):
    """An app over a disposable copy of the sample root so a test may damage its inputs."""
    duplicate = tmp_path / 'samples'
    shutil.copytree(settings.sample_root, duplicate)
    scratch = Settings(sample_root=duplicate, database=settings.database)
    with TestClient(create_app(scratch)) as session:
        yield session, scratch


def shelf(run, key):
    return locate_or_fail(run['penalties']['rows'], key=key)


def test_filed_substitute_on_shelf_offsets_only_the_missing_sku(substitute_run):
    applied = shelf(substitute_run, '001:A02')
    assert (applied['status'], applied['reason']) == ('COMPLIANT', 'FILING_APPLIED')
    assert (applied['assessed_cents'], applied['offset_cents'], applied['original_cents']) == (15000, 15000, 0)
    assert applied['filing']['filing'] == 'F-001'
    assert applied['filing']['filing_plan'] == 'OLD-210'
    assert applied['filing']['filing_plan_source'] == 'plans.csv:2'
    assert applied['filing']['substitute_on_shelf'] is True


def test_substitute_partly_clears_wrong_sku_but_keeps_extra_goods_fined(substitute_run):
    row = shelf(substitute_run, '004:A02')
    assert (row['status'], row['reason']) == ('VIOLATION', 'WRONG_SKU')
    assert (row['assessed_cents'], row['offset_cents'], row['original_cents']) == (15000, 6000, 9000)
    assert row['applied']['proof']['skus'] == ['MALT-900']
    assert row['filing']['verdict'] == 'APPLIED'


def test_empty_shelf_is_never_excused_by_a_filing(substitute_run):
    row = shelf(substitute_run, '005:A01')
    assert (row['status'], row['reason'], row['original_cents']) == ('VIOLATION', 'MISSING_SKU', 15000)
    assert row['filing']['verdict'] == 'NOT_FILED'


def test_filing_without_the_substitute_on_shelf_still_fined(substitute_run):
    row = shelf(substitute_run, '001:A04')
    assert (row['status'], row['reason'], row['original_cents']) == ('VIOLATION', 'SUBSTITUTE_ABSENT', 15000)
    assert row['filing']['filing'] == 'F-003' and row['filing']['substitute_on_shelf'] is False


def test_substitute_below_filed_facings_does_not_offset(substitute_run):
    row = shelf(substitute_run, '001:A03')
    assert (row['status'], row['reason'], row['original_cents']) == ('VIOLATION', 'FILING_TERMS_NOT_MET', 15000)
    assert row['filing']['observed']['facings'] == 2 and row['filing']['filing_facings'] == 4


def test_cross_store_filing_cannot_offset(substitute_run):
    row = shelf(substitute_run, '002:A01')
    assert (row['status'], row['reason'], row['original_cents']) == ('VIOLATION', 'FILING_STORE_MISMATCH', 15000)
    assert row['filing']['filing'] == 'F-001' and row['filing']['filing_store'] == '001'
    assert row['filing']['near_misses'][0]['filing_store'] == '001'
    assert row['original_cents'] == 15000


def test_cross_slot_filing_cannot_offset(substitute_run):
    row = shelf(substitute_run, '002:A03')
    assert (row['status'], row['reason']) == ('VIOLATION', 'FILING_SLOT_MISMATCH')
    assert row['filing']['filing'] == 'F-005' and row['filing']['filing_slot'] == 'A02'
    assert row['filing']['verdict'] == 'SLOT_MISMATCH'


def test_expired_filing_cannot_offset(substitute_run):
    row = shelf(substitute_run, '002:A04')
    assert (row['status'], row['reason']) == ('VIOLATION', 'FILING_EXPIRED')
    assert row['filing']['window']['end_utc'].startswith('2026-11-05')


def test_filing_for_another_plan_version_cannot_offset(substitute_run):
    row = shelf(substitute_run, '003:A04')
    assert (row['status'], row['reason']) == ('VIOLATION', 'FILING_PLAN_MISMATCH')
    assert row['filing']['filing_plan'] == 'OLD-210' and row['filing']['effective_plan'] == 'NEW-212'


def test_pending_or_isolated_evidence_blocks_the_offset(substitute_run):
    pending = shelf(substitute_run, '005:A03')
    isolated = shelf(substitute_run, '005:A02')
    assert (pending['status'], pending['reason'], pending['offset_cents']) == ('PENDING', 'SCAN_INCOMPLETE', 0)
    assert (isolated['status'], isolated['reason'], isolated['offset_cents']) == ('ISOLATED', 'OBSERVATION_CONFLICT', 0)
    assert (pending['filing']['verdict'], isolated['filing']['verdict']) == ('UNVERIFIED', 'BLOCKED')


def test_present_required_sku_needs_no_substitute(substitute_run):
    row = shelf(substitute_run, '003:A01')
    assert (row['status'], row['filing']['verdict'], row['filing']['filing']) == ('COMPLIANT', 'NOT_REQUIRED', 'F-004')


def test_audit_without_a_filings_file_keeps_every_penalty(field, client):
    snapshot = client.post('/api/settlement/runs', json={}).json()
    assert snapshot['penalties']['filing_present'] is False
    assert snapshot['settlement']['assessed_cents'] == snapshot['settlement']['original_cents'] == 29500
    assert snapshot['settlement']['offsets'] == []


def test_filing_ledger_matches_source_lines_and_isolates_bad_rows(substitute_run):
    sheet = substitute_run['settlement']
    assert sheet['sources']['filings'] == {'input': 17, 'accepted': 14, 'isolated': 3, 'blank': 0, 'repeated_headers': 0}
    reasons = {row['reason'] for row in substitute_run['filings']['quarantine']}
    assert reasons == {'UNKNOWN_PLAN', 'UNKNOWN_STORE', 'DUPLICATE_ROW'}
    assert sheet['balanced'] is True


def test_finance_totals_reconcile_through_appeals(substitute_run):
    appeals = substitute_run['appeals']
    assert (appeals['assessed_cents'], appeals['offset_cents'], appeals['original_cents']) == (180000, 66000, 114000)
    assert appeals['finance'] == {'assessed_cents': 180000, 'filing_offset_cents': 66000,
                                  'penalty_cents': 114000, 'appeal_reduction_cents': 0, 'payable_cents': 114000}
    assert appeals['balanced'] is True


def test_rerun_does_not_offset_the_same_filing_twice(client, substitute_run):
    again = client.post('/api/settlement/runs', json={'dataset': 'substitute'}).json()
    assert again['reused'] is True
    assert again['manifest']['run_id'] == substitute_run['manifest']['run_id']
    assert again['settlement']['offset_cents'] == 66000
    assert sum(row['offset_cents'] for row in again['penalties']['rows']) == 66000


def test_stored_batch_keeps_the_filing_version_evidence_after_source_change(scratch_client):
    client, scratch = scratch_client
    stored = client.post('/api/settlement/runs', json={'dataset': 'substitute'}).json()
    run_id = stored['manifest']['run_id']
    assert stored['settlement']['offset_cents'] == 66000
    assert stored['manifest']['inputs']['filings.csv']
    assert stored['filings']['rows'][0]['source']['id'].startswith('filings.csv:')
    applied = locate_or_fail(stored['penalties']['rows'], key='001:A02')
    assert applied['filing']['filing_plan'] == 'OLD-210'
    # Even after the files on disk change, the generated batch still answers from its own snapshot.
    target = scratch.sample_root / 'substitute' / 'filings.csv'
    target.write_bytes(target.read_bytes().replace(b'F-001,001,A02,OLD-210', b'F-001,001,A02,NEW-212'))
    reread = client.get(f'/api/settlement/runs/{run_id}').json()
    assert reread == {k: v for k, v in stored.items() if k != 'reused'}


def test_generation_failure_keeps_previous_batch(scratch_client):
    client, scratch = scratch_client
    run = client.post('/api/settlement/runs', json={'dataset': 'substitute'}).json()
    run_id = run['manifest']['run_id']
    assert run['settlement']['offset_cents'] == 66000
    target = scratch.sample_root / 'substitute' / 'filings.csv'
    target.write_bytes(target.read_bytes() + b'F-CUT,001,A01,"cut')
    broken = client.post('/api/settlement/runs', json={'dataset': 'substitute'})
    assert (broken.status_code, broken.json()['detail']['code']) == (422, 'MALFORMED_CSV')
    old = client.get(f'/api/settlement/runs/{run_id}').json()
    assert old['settlement']['offset_cents'] == 66000 and old['manifest']['run_id'] == run_id


def test_changed_filing_creates_a_new_batch_not_a_reused_offset(settings, tmp_path):
    duplicate = tmp_path / 'data'
    shutil.copytree(settings.sample_root, duplicate)
    settings.sample_root = duplicate
    store = Store(settings.database)
    store.initialize()
    first, reused = run_audit(settings, store, Options(dataset='substitute'))
    assert not reused and first['settlement']['offset_cents'] == 66000
    target = duplicate / 'substitute' / 'filings.csv'
    target.write_bytes(target.read_bytes().replace(b'F-001,001,A02,OLD-210,ALT-301,4,13800',
                                                   b'F-001,001,A02,OLD-210,ALT-301,9,13800'))
    second, reused = run_audit(settings, store, Options(dataset='substitute'))
    assert not reused and second['manifest']['run_id'] != first['manifest']['run_id']
    applied = locate_or_fail(second['penalties']['rows'], key='001:A02')
    assert (applied['offset_cents'], applied['original_cents'], applied['filing']['verdict']) == (0, 15000, 'TERMS_NOT_MET')
    assert locate_or_fail(first['penalties']['rows'], key='001:A02')['offset_cents'] == 15000


def test_fully_offset_shelf_has_no_appealable_penalty(client, substitute_run):
    run_id = substitute_run['manifest']['run_id']
    response = client.post(f'/api/appeals/{run_id}/events', json={**appeal_payload(), 'key': '001:A02'})
    assert (response.status_code, response.json()['detail']['code']) == (409, 'NO_PENALTY')


def test_export_carries_offsets_versions_and_filing_sources(client, substitute_run):
    exported = client.get(f"/api/appeals/{substitute_run['manifest']['run_id']}/export").json()
    assert exported['appeals']['finance']['filing_offset_cents'] == 66000
    assert exported['settlement']['sources']['filings']['accepted'] == 14
    applied = locate_or_fail(exported['settlement']['offsets'], key='004:A02')
    assert (applied['filing'], applied['plan'], applied['plan_source']) == ('F-008', 'NEW-212', 'plans.csv:3')
    assert applied['assessed_cents'] - applied['offset_cents'] == 9000
    assert exported['filings']['rows'][0]['sku'].startswith('ALT-')


def test_filings_endpoint_previews_per_shelf_decisions(client):
    preview = client.get('/api/filings?dataset=substitute').json()
    assert preview['present'] is True and len(preview['rows']) == 14
    row = locate_or_fail(preview['shelves'], key='006:A02')
    assert (row['verdict'], row['filing'], row['offset_cents']) == ('APPLIED', 'F-012', 15000)


def test_filing_must_match_the_plan_version_in_force_at_the_visit(substitute_run):
    # 006:A02 files against OLD-210 and the visit is before the rollover: still in force.
    applied = shelf(substitute_run, '006:A02')
    assert applied['filing']['filing_plan'] == applied['filing']['effective_plan'] == 'OLD-210'
    assert applied['filing']['visit_at_utc'] == '2026-11-08T15:55:00+00:00'
    # 003:A04's visit is after the rollover, so the OLD-210 filing cannot answer it.
    stale = shelf(substitute_run, '003:A04')
    assert stale['filing']['visit_at_utc'] == '2026-11-09T00:00:00+00:00'
    assert stale['filing']['verdict'] == 'PLAN_MISMATCH'


def test_filing_windows_are_half_open(substitute_run):
    expired = shelf(substitute_run, '002:A04')
    assert expired['filing']['window'] == {'start_utc': '2026-11-01T00:00:00+00:00',
                                           'end_utc': '2026-11-05T00:00:00+00:00'}
    assert expired['filing']['verdict'] == 'EXPIRED'


def test_filing_scope_is_exact_not_prefix_matched(substitute_run):
    # F-001 covers 001:A02; neither 001:A03 nor 002:A01 may borrow it.
    assert shelf(substitute_run, '001:A03')['filing']['filing'] == 'F-002'
    cross = shelf(substitute_run, '002:A01')['filing']
    assert cross['filing'] == 'F-001' and cross['verdict'] == 'STORE_MISMATCH'
    assert cross['near_misses'][0]['filing_store'] == '001' and cross['near_misses'][0]['filing_slot'] == 'A02'


def test_only_missing_sku_is_excused_never_facings_or_price():
    from app.modules.penalties.service import excuse
    matched = [{'code': 'UNDER_FACINGS', 'cents': 5500, 'proof': {'sources': ['obs:1']}},
               {'code': 'PRICE_MISMATCH', 'cents': 4000, 'proof': {'sources': ['obs:1']}}]
    assert excuse(matched, {'verdict': 'NOT_FILED'}, {}) == matched
    applied = {'verdict': 'APPLIED', 'filing_sku': 'ALT-301'}
    assert excuse(matched, applied, {}) == matched
    missing = [{'code': 'MISSING_SKU', 'cents': 15000, 'proof': {}},
               {'code': 'WRONG_SKU', 'cents': 9000, 'proof': {'skus': ['ALT-301'], 'sources': []}}]
    assert excuse(missing, applied, {}) == []
    wrong = [{'code': 'WRONG_SKU', 'cents': 9000, 'proof': {'skus': ['ALT-301', 'X-1'], 'sources': []}}]
    kept = excuse(wrong, applied, {'X-1': {'sources': ['obs:9']}})
    assert kept[0]['proof'] == {'skus': ['X-1'], 'sources': ['obs:9']}


def test_appeal_reduction_is_capped_at_the_offset_balance(client, substitute_run):
    url = f"/api/appeals/{substitute_run['manifest']['run_id']}/events"
    opened = client.post(url, json={**appeal_payload(), 'key': '004:A02'})
    assert opened.status_code == 200
    event = opened.json()['event']
    assert (event['original_cents'], event['original_rule']) == (9000, 'WRONG_SKU')
    full = appeal_payload(key='004:A02', request_id='PART-FULL-1', expected_version=1, action='REDUCED',
                          reduction_cents=9000)
    rejected = client.post(url, json=full)
    assert (rejected.status_code, rejected.json()['detail']['code']) == (422, 'BAD_REDUCTION')
    # The rejected attempt must not advance the stage; a fresh request still sees version 1.
    waived = client.post(url, json={**appeal_payload(key='004:A02', request_id='PART-FULL-2', expected_version=1,
                                                   action='WAIVED')})
    assert waived.status_code == 200 and waived.json()['event']['reduction_cents'] == 9000
    balance = client.get(f"/api/appeals/{substitute_run['manifest']['run_id']}").json()
    assert (balance['offset_cents'], balance['net_cents']) == (66000, 105000)


def test_rerun_keeps_appeal_events_alongside_the_filing_offsets(client, substitute_run):
    run_id = substitute_run['manifest']['run_id']
    client.post(f'/api/appeals/{run_id}/events', json={**appeal_payload(), 'key': '002:A03'})
    replay = client.post('/api/settlement/runs', json={'dataset': 'substitute'}).json()
    assert replay['reused'] is True
    assert [event['status'] for event in replay['events']] == ['OPEN']
    assert replay['settlement']['offset_cents'] == 66000


def test_dataset_given_to_filings_endpoint_still_needs_a_valid_option(client):
    bad = client.get('/api/filings?dataset=nope')
    assert (bad.status_code, bad.json()['detail']['code']) == (422, 'INVALID_INPUT')


def test_substitute_dataset_photos_read_with_matching_digest(client, substitute_run):
    photo = locate_or_fail(substitute_run['photos']['photos'], photo='P001')
    response = client.get(f"/api/photos/original/substitute/P001.png/{photo['sha256']}")
    assert response.status_code == 200 and response.headers['content-type'] == 'image/png'
    wrong = client.get(f"/api/photos/original/substitute/P001.png/{'0' * 64}")
    assert (wrong.status_code, wrong.json()['detail']['code']) == (409, 'PHOTO_CHANGED')


def test_one_filing_cannot_offset_two_shelves(substitute_run):
    applied = [row for row in substitute_run['penalties']['rows'] if row['offset_cents']]
    filings = [row['filing']['filing'] for row in applied]
    assert len(filings) == len(set(filings)) == 5
    assert all(row['filing']['verdict'] == 'APPLIED' for row in applied)
    assert all('MISSING_SKU' in {c['code'] for c in row['candidates']} for row in applied)
    assert len(substitute_run['settlement']['offsets']) == 5


def test_identical_duplicate_filing_rows_do_not_double_offset(settings, tmp_path):
    duplicate = tmp_path / 'samples'
    shutil.copytree(settings.sample_root, duplicate)
    table = duplicate / 'substitute' / 'filings.csv'
    lines = table.read_text(encoding='utf-8').splitlines()
    table.write_text('\n'.join(lines + [lines[1], lines[1]]) + '\n', encoding='utf-8')
    audit = Audit(Settings(sample_root=duplicate, database=settings.database), Options(dataset='substitute'))
    sheet = audit.settlement
    assert (sheet['assessed_cents'], sheet['offset_cents'], sheet['original_cents']) == (180000, 66000, 114000)
    applied = locate_or_fail(audit.penalties['rows'], key='001:A02')
    assert (applied['filing']['filing'], applied['offset_cents'], applied['filing']['alternates']) == ('F-001', 15000, [])
    assert sheet['sources']['filings'] == {'input': 19, 'accepted': 14, 'isolated': 5, 'blank': 0, 'repeated_headers': 0}
    assert {row['reason'] for row in audit.filings['quarantine']} == {'DUPLICATE_ROW', 'UNKNOWN_PLAN', 'UNKNOWN_STORE'}


def test_filings_preview_on_plain_field_archive_reports_absent(client):
    preview = client.get('/api/filings?dataset=field').json()
    assert (preview['present'], preview['rows'], preview['quarantine'], preview['scopes']) == (False, [], [], {})
    assert (preview['assessed_cents'], preview['offset_cents'], preview['original_cents']) == (29500, 0, 29500)
    assert len(preview['shelves']) == 17
    assert all(row['verdict'] in ('NOT_FILED', 'NOT_REQUIRED') and row['offset_cents'] == 0
               for row in preview['shelves'])
