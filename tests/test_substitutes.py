from copy import deepcopy

import pytest

from app.config import Options
from app.errors import AuditError
from app.modules.coverage.service import coverage
from app.modules.intake.csv_reader import read_csv
from app.modules.penalties.service import judge
from app.modules.photos.service import collect_evidence
from app.modules.settlement.reconcile import reconcile
from app.modules.substitutes.service import substitute_clearance
from conftest import locate_or_fail

HEADER = 'filing,version,store,slot,expected_sku,substitute_sku,start_utc,end_utc\n'


def with_filings(audit, lines):
    """改了 bundle 的用例需要重建证据、覆盖，再套用备案裁决与罚则。"""
    intake = deepcopy(audit.intake)
    intake['tables']['substitutes'] = read_csv((HEADER + ''.join(lines)).encode('utf-8'), 'substitutes')
    photos = collect_evidence(intake, audit.plans, audit.bundle)
    sample = audit.options.sample_size or intake['campaign']['sample_size']
    rebuilt = coverage(intake, audit.plans, photos, sample)
    clearances = substitute_clearance(intake, audit.plans, rebuilt)
    penalties = judge(rebuilt, intake['campaign'], clearances)
    return intake, clearances, penalties, rebuilt, photos


def decision(clearances, key):
    return locate_or_fail(clearances['rows'], key=key)


def test_valid_filing_with_substitute_on_shelf_offsets_missing_sku(reference):
    # 002:A03 应陈 GR-211；备案允许 MALT-900 顶柜，且现场确实上架
    lines = ['SUB-T1,V1,002,A03,GR-211,MALT-900,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n']
    # 先在现场清点中制造缺品＋替代品上架
    reference.bundle['observations.csv'] = reference.bundle['observations.csv'].replace(
        b'V002,A03,P007,ITEM,\xe6\x96\xb0\xe8\xb0\xb7\xe9\x99\x88\xe9\x85\xbf,3,15800',
        b'V002,A03,P007,ITEM,MALT-900,2,21900')
    intake, clearances, penalties, rebuilt, photos = with_filings(reference, lines)
    row = locate_or_fail(penalties['rows'], key='002:A03')
    assert row['reason'] == 'MISSING_SKU'
    assert (row['original_cents'], row['offset_cents'], row['net_cents']) == (15000, 15000, 0)
    verdict = decision(clearances, '002:A03')
    assert verdict['status'] == 'FILING_OK' and verdict['on_shelf'] is True
    assert (verdict['filing']['filing'], verdict['filing']['version']) == ('SUB-T1', 'V1')
    assert verdict['sources'][0].startswith('substitutes.csv:')


def test_filing_without_substitute_on_shelf_keeps_full_penalty(reference):
    # 只认备案、现场空柜也要放过的反例：替代品未上架，罚款保留
    lines = ['SUB-T2,V1,002,A03,GR-211,MALT-900,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n']
    intake, clearances, penalties, rebuilt, photos = with_filings(reference, lines)
    row = locate_or_fail(penalties['rows'], key='002:A03')
    assert row['offset_cents'] == 0 and row['net_cents'] == row['original_cents']
    verdict = decision(clearances, '002:A03')
    assert verdict['status'] == 'FILING_NOT_PRESENT' and verdict['on_shelf'] is False


def test_cross_store_filing_cannot_offset(field):
    # 备案属于 003，拿到 002 的柜上不能算数
    lines = ['SUB-T3,V1,003,A03,GR-211,MALT-900,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n']
    _, clearances, _, _, _ = with_filings(field, lines)
    assert decision(clearances, '002:A03')['status'] == 'FILING_SCOPE_STORE'


def test_cross_slot_filing_cannot_offset(field):
    # 同店但备案写的是 A01，A03 不能引用
    lines = ['SUB-T4,V1,002,A01,GR-211,MALT-900,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n']
    _, clearances, _, _, _ = with_filings(field, lines)
    assert decision(clearances, '002:A03')['status'] == 'FILING_SCOPE_SLOT'


def test_expired_filing_cannot_offset(field):
    # 巡检 2026-11-09，备案 11-08 已截止（左闭右开，边界等于截止即失效）
    lines = ['SUB-T5,V1,002,A03,GR-211,MALT-900,2026-10-01T00:00:00Z,2026-11-08T00:00:00Z\n']
    _, clearances, _, _, _ = with_filings(field, lines)
    assert decision(clearances, '002:A03')['status'] == 'FILING_NOT_ACTIVE'


def test_overlapping_filing_versions_block_offset(field):
    lines = [
        'SUB-T6,V1,002,A03,GR-211,MALT-900,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n',
        'SUB-T6,V2,002,A03,GR-211,MALT-900,2026-11-01T00:00:00Z,2026-12-15T00:00:00Z\n',
    ]
    _, clearances, _, _, _ = with_filings(field, lines)
    verdict = decision(clearances, '002:A03')
    assert verdict['status'] == 'FILING_OVERLAP'
    assert {item['version'] for item in verdict['candidates']} == {'V1', 'V2'}


def test_same_filing_used_by_two_slots_only_offsets_once(reference):
    # 同一备案编号＋版本同时满足两个柜，运行内只能抵扣一次，第二柜标记重复使用
    reference.bundle['observations.csv'] = reference.bundle['observations.csv'].replace(
        b'V002,A03,P007,ITEM,\xe6\x96\xb0\xe8\xb0\xb7\xe9\x99\x88\xe9\x85\xbf,3,15800',
        b'V002,A03,P007,ITEM,MALT-900,2,21900').replace(
        b'V002,A04,P008,ITEM,GR-211,3,15800',
        b'V002,A04,P008,ITEM,MALT-900,2,21900')
    lines = [
        'SUB-T7,V1,002,A03,GR-211,MALT-900,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n',
        'SUB-T7,V1,002,A04,GR-211,MALT-900,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n',
    ]
    _, clearances, penalties, rebuilt, photos = with_filings(reference, lines)
    eligible = [row for row in clearances['rows'] if row['eligible']]
    blocked = [row for row in clearances['rows'] if row['status'] == 'FILING_DUPLICATE_USE']
    assert len(eligible) == 1 and len(blocked) == 1
    assert blocked[0]['reused_by'] == eligible[0]['key']
    total_offset = sum(row['offset_cents'] for row in penalties['rows'])
    assert total_offset == 15000


def test_under_facings_and_price_mismatch_are_not_offsettable(reference):
    # 001:A03 改造成排面不足，同时让备案替代品 MALT-900 也在架；排面罚则仍不允许用备案抵消
    reference.bundle['observations.csv'] = reference.bundle['observations.csv'].replace(
        b'V001,A03,P003,ITEM,\xe8\xb0\xb7\xe9\x9b\xa8\xe9\x99\x88\xe9\x85\xbf,4,13800',
        b'V001,A03,P003,ITEM,GR-209,3,13800\r\nV001,A03,P003,ITEM,MALT-900,1,21900')
    lines = ['SUB-T8,V1,001,A03,GR-209,MALT-900,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n']
    _, clearances, penalties, rebuilt, photos = with_filings(reference, lines)
    # 抬高排面罚额，确保执行项是 UNDER_FACINGS 而非乱陈
    campaign = deepcopy(reference.intake['campaign'])
    campaign['penalty_cents'] = {**campaign['penalty_cents'], 'UNDER_FACINGS': 99000}
    penalties = judge(rebuilt, campaign, clearances)
    row = locate_or_fail(penalties['rows'], key='001:A03')
    assert row['reason'] == 'UNDER_FACINGS' and row['offset_cents'] == 0
    # 备案本身有效且替代品在架，但罚则层拒绝抵扣并留痕
    assert decision(clearances, '001:A03')['status'] == 'FILING_OK'
    assert row['clearance']['status'] == 'FILING_RULE_NOT_OFFSETTABLE'


def test_conflicting_filing_version_is_quarantined(reference):
    lines = [
        'SUB-T9,V1,002,A03,GR-211,MALT-900,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n',
        'SUB-T9,V1,002,A03,GR-211,RYE-700,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n',
    ]
    _, clearances, _, _, _ = with_filings(reference, lines)
    assert {row['reason'] for row in clearances['quarantine']} == {'FILING_VERSION_CONFLICT'}
    assert decision(clearances, '002:A03')['status'] == 'NO_FILING'


def test_same_sku_and_bad_interval_filings_are_rejected(reference):
    lines = [
        'SUB-TA,V1,002,A03,GR-211,GR-211,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n',
        'SUB-TB,V1,002,A03,GR-211,MALT-900,2026-12-01T00:00:00Z,2026-11-01T00:00:00Z\n',
    ]
    _, clearances, _, _, _ = with_filings(reference, lines)
    assert {row['reason'] for row in clearances['quarantine']} == {'FILING_SAME_SKU', 'FILING_BAD_INTERVAL'}


def test_reconcile_balances_gross_offset_and_net(reference):
    reference.bundle['observations.csv'] = reference.bundle['observations.csv'].replace(
        b'V002,A03,P007,ITEM,\xe6\x96\xb0\xe8\xb0\xb7\xe9\x99\x88\xe9\x85\xbf,3,15800',
        b'V002,A03,P007,ITEM,MALT-900,2,21900')
    lines = ['SUB-TC,V1,002,A03,GR-211,MALT-900,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n']
    intake, clearances, penalties, rebuilt, photos = with_filings(reference, lines)
    sheet = reconcile(intake, reference.plans, photos, rebuilt, penalties, clearances)
    assert (sheet['original_cents'], sheet['offset_cents'], sheet['net_cents']) == (15000, 15000, 0)
    assert sheet['sources']['substitutes'] == {'input': 1, 'accepted': 1, 'isolated': 0, 'blank': 0,
                                                'repeated_headers': 0}


def test_reconcile_detects_offset_without_filing_basis(reference):
    # 制造一笔缺品违规但不给备案，再人为塞抵扣，勾稽必须拦下
    reference.bundle['observations.csv'] = reference.bundle['observations.csv'].replace(
        b'V002,A03,P007,ITEM,\xe6\x96\xb0\xe8\xb0\xb7\xe9\x99\x88\xe9\x85\xbf,3,15800',
        b'V002,A03,P007,ITEM,MALT-900,2,21900')
    intake, clearances, penalties, rebuilt, photos = with_filings(reference, [])
    row = next(item for item in penalties['rows'] if item['status'] == 'VIOLATION')
    row['offset_cents'] = row['original_cents']
    row['net_cents'] = 0
    with pytest.raises(AuditError) as error:
        reconcile(intake, reference.plans, photos, rebuilt, penalties, clearances)
    assert error.value.code == 'FILING_LEDGER_MISMATCH'


def test_duplicate_use_outcome_is_stable_under_input_permutation(reference):
    reference.bundle['observations.csv'] = reference.bundle['observations.csv'].replace(
        b'V002,A03,P007,ITEM,\xe6\x96\xb0\xe8\xb0\xb7\xe9\x99\x88\xe9\x85\xbf,3,15800',
        b'V002,A03,P007,ITEM,MALT-900,2,21900').replace(
        b'V002,A04,P008,ITEM,GR-211,3,15800',
        b'V002,A04,P008,ITEM,MALT-900,2,21900')
    lines = [
        'SUB-T7,V1,002,A03,GR-211,MALT-900,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n',
        'SUB-T7,V1,002,A04,GR-211,MALT-900,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n',
    ]
    _, first, _, _, _ = with_filings(reference, lines)
    # 打乱门店与柜位名册输入顺序，备案结论与占用柜位必须保持一致
    reference.intake['slots'].reverse()
    reference.intake['stores'].reverse()
    _, shuffled, _, _, _ = with_filings(reference, list(reversed(lines)))
    assert [(r['key'], r['status']) for r in first['rows']] == [(r['key'], r['status']) for r in shuffled['rows']]
    assert locate_or_fail(first['rows'], key='002:A03')['eligible'] is True
    assert locate_or_fail(first['rows'], key='002:A04')['status'] == 'FILING_DUPLICATE_USE'


def test_pending_and_isolated_slots_never_receive_offset(field):
    # 001:A04 待证缺图、001:A02 照片复用隔离；即使备案形式有效也不产生任何抵扣
    lines = [
        'SUB-P1,V1,001,A04,GR-209,MALT-900,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n',
        'SUB-P2,V1,001,A02,GR-209,MALT-900,2026-10-01T00:00:00Z,2026-12-01T00:00:00Z\n',
    ]
    _, clearances, penalties, _, _ = with_filings(field, lines)
    for key, state in (('001:A04', 'PENDING'), ('001:A02', 'ISOLATED')):
        row = locate_or_fail(penalties['rows'], key=key)
        assert row['status'] == state and row['offset_cents'] == 0 and row['net_cents'] == 0
        assert locate_or_fail(clearances['rows'], key=key)['eligible'] is False


def test_field_dataset_preserves_finance_totals_with_all_filings_rejected(field):
    # field 备案表覆盖跨店、跨柜、过期、叠版、未上架，但没有一笔能抵，财务罚额保持 295 元
    sheet = field.settlement
    assert (sheet['original_cents'], sheet['offset_cents'], sheet['net_cents']) == (29500, 0, 29500)
    statuses = {row['status'] for row in field.clearances['rows']}
    assert {'FILING_SCOPE_STORE', 'FILING_SCOPE_SLOT', 'FILING_NOT_ACTIVE', 'FILING_OVERLAP',
            'FILING_NOT_PRESENT'} <= statuses


def test_generated_batch_keeps_filing_basis_for_finance(client, run):
    # 已生成批次里每个柜都带备案裁决、依据版本和现场核验结果，可直接交财务追溯
    snapshot = client.get(f"/api/settlement/runs/{run['manifest']['run_id']}").json()
    assert len(snapshot['clearances']['rows']) == snapshot['settlement']['selected']
    for row in snapshot['penalties']['rows']:
        verdict = row['clearance']
        assert {'status', 'eligible', 'filing', 'substitute_sku', 'on_shelf', 'sources'} <= set(verdict)
        assert row['net_cents'] == row['original_cents'] - row['offset_cents']
    # 导出证据包含同一份逐柜备案裁决和原因字典
    exported = client.get(f"/api/appeals/{run['manifest']['run_id']}/export").json()
    assert exported['settlement']['net_cents'] == run['settlement']['net_cents']
    assert 'FILING_NOT_PRESENT' in exported['reason_dictionary']


def test_failed_generation_keeps_previous_batch_and_rerun_reuses_it(client, run, settings, tmp_path):
    import shutil

    from app.modules.settlement.service import run_audit
    run_id = run['manifest']['run_id']
    original_root = settings.sample_root
    duplicate = tmp_path / 'data'
    shutil.copytree(original_root, duplicate)
    settings.sample_root = duplicate
    # 损坏备案表，令下一次生成在解析阶段失败；旧批次必须原样保留
    (duplicate / 'field' / 'substitutes.csv').write_bytes(b'filing,version\n"BAD')
    with pytest.raises(AuditError):
        run_audit(settings, client.app.state.store, Options())
    stored = client.get(f'/api/settlement/runs/{run_id}').json()
    assert stored['manifest']['run_id'] == run_id
    assert stored['settlement']['net_cents'] == run['settlement']['net_cents']
    # 修好原件后同参数重跑，复用既有批次，不产生重复抵扣或新批次
    settings.sample_root = original_root
    snapshot, reused = run_audit(settings, client.app.state.store, Options())
    assert reused and snapshot['manifest']['run_id'] == run_id
