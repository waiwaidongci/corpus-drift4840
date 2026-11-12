"""End-to-end checks for plan versioning, optimistic concurrency and exports.

Run with: DATABASE_URL=sqlite:////tmp/yanyu-test.db JWT_SECRET=test \
  /tmp/yanyu-venv/bin/python -I -m pytest tests/test_plan_versions.py
"""
import os
import tempfile

os.environ.setdefault('DATABASE_URL', 'sqlite:///' + tempfile.mkdtemp() + '/test.db')
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('SEED_DEMO', 'false')
os.environ.pop('REDIS_URL', None)
os.environ['REDIS_URL'] = 'redis://localhost:6379/0'

import pytest
from fastapi.testclient import TestClient

from src.config.database import Base, engine, SessionLocal
from src.models import User, CorpusSnapshot, Utterance, ComparisonRun, DriftFinding, ResamplingPlan
from src.main import app
from src.utils.security import hash_password, issue_token


@pytest.fixture(scope='module')
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        admin = User(email='admin@test', name='负责人', role='admin', password_hash=hash_password('x'))
        analyst = User(email='analyst@test', name='研究员', role='analyst', password_hash=hash_password('x'))
        other = User(email='analyst2@test', name='研究员二', role='analyst', password_hash=hash_password('x'))
        db.add_all([admin, analyst, other])
        ref = CorpusSnapshot(name='ref', kind='reference', filename='r.csv')
        cand = CorpusSnapshot(name='cand', kind='candidate', filename='c.csv')
        db.add_all([ref, cand]); db.flush()
        for i in range(60):
            db.add(Utterance(snapshot_id=ref.id, reference=f'R{i}', features={'duration': i, 'noise': i, 'token_rate': i, 'language': 'zh', 'device': 'studio'}))
            db.add(Utterance(snapshot_id=cand.id, reference=f'C{i}', features={'duration': i + 1, 'noise': i, 'token_rate': i, 'language': 'zh', 'device': 'mobile'}))
        run = ComparisonRun(name='run', reference_id=ref.id, candidate_id=cand.id, status='succeeded', config={}, result={}, creator_id=admin.id)
        db.add(run); db.flush()
        for feat in ('duration', 'noise', 'token_rate', 'language', 'device'):
            db.add(DriftFinding(run_id=run.id, feature=feat, status='review', metrics={'psi': .1, 'js': .01, 'p_value': .5, 'q_value': .5, 'fdr_reject': False}))
        db.commit()
    with TestClient(app) as c:
        yield c


CLASS = 'tests.test_plan_versions'


def login(client, email):
    with SessionLocal() as db:
        u = db.query(User).filter(User.email == email).one()
        return {'Authorization': 'Bearer ' + issue_token(u)}


def seed_plan(client, finding_ids=(1,)):
    r = client.post('/plans', json={'run_id': 1, 'name': '草案', 'finding_ids': list(finding_ids), 'target_slice': 'studio', 'sample_target': 200, 'rationale': '理由'}, headers=login(client, 'analyst@test'))
    assert r.status_code == 201, r.text
    return r.json()


def test_create_has_version(client):
    p = seed_plan(client)
    assert p['current_version'] == 1 and p['effective_version'] == 1
    assert p['versions'][0]['version'] == 1 and p['versions'][0]['finding_ids'] == [1]


def test_edit_creates_new_version_and_preserves_old(client):
    p = seed_plan(client, (1, 2))
    r = client.patch(f"/plans/{p['id']}", json={'version': 1, 'name': 'v2 草案', 'finding_ids': [2, 3], 'target_slice': 'mobile', 'sample_target': 300, 'rationale': '新理由'}, headers=login(client, 'analyst@test'))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d['current_version'] == 2 and d['status'] == 'draft'
    v1 = next(v for v in d['versions'] if v['version'] == 1)
    assert v1['name'] == '草案' and v1['finding_ids'] == [1, 2]
    assert d['versions'][-1]['finding_ids'] == [2, 3]


def test_stale_edit_rejected(client):
    p = seed_plan(client)
    assert client.patch(f"/plans/{p['id']}", json={'version': 1, 'name': 'a', 'finding_ids': [1], 'target_slice': 's', 'sample_target': 200, 'rationale': 'r'}, headers=login(client, 'analyst@test')).status_code == 200
    r = client.patch(f"/plans/{p['id']}", json={'version': 1, 'name': 'b', 'finding_ids': [1], 'target_slice': 's', 'sample_target': 200, 'rationale': 'r'}, headers=login(client, 'analyst2@test'))
    assert r.status_code == 409
    assert '过期' in r.json()['detail'] and 'v2' in r.json()['detail']


def test_rejected_plan_can_be_revised_and_needs_new_review(client):
    p = seed_plan(client)
    r = client.patch(f"/plans/{p['id']}/approval", json={'version': 1, 'status': 'rejected', 'review_note': '来源没核对'}, headers=login(client, 'admin@test'))
    assert r.status_code == 200
    d = r.json(); assert d['status'] == 'rejected'
    assert d['versions'][0]['status'] == 'rejected' and d['versions'][0]['review_note'] == '来源没核对'
    r2 = client.patch(f"/plans/{d['id']}", json={'version': 1, 'name': '补采 v2', 'finding_ids': [1], 'target_slice': 'studio', 'sample_target': 200, 'rationale': '已核对来源'}, headers=login(client, 'analyst@test'))
    assert r2.status_code == 200
    d2 = r2.json(); assert d2['status'] == 'draft' and d2['current_version'] == 2 and d2['review_note'] == ''
    assert d2['versions'][0]['review_note'] == '来源没核对'


def test_approval_freezes_and_export_uses_approved_version(client):
    p = seed_plan(client)
    client.patch(f"/plans/{p['id']}", json={'version': 1, 'name': 'v2 名称', 'finding_ids': [1], 'target_slice': 'mobile', 'sample_target': 300, 'rationale': 'v2 理由'}, headers=login(client, 'analyst@test'))
    r = client.patch(f"/plans/{p['id']}/approval", json={'version': 2, 'status': 'approved', 'review_note': '同意 v2'}, headers=login(client, 'admin@test'))
    assert r.status_code == 200, r.text
    d = r.json(); assert d['status'] == 'approved' and d['approved_version'] == 2 and d['effective_version'] == 2
    # editing an approved plan is refused
    r2 = client.patch(f"/plans/{p['id']}", json={'version': 2, 'name': 'v3', 'finding_ids': [1], 'target_slice': 'x', 'sample_target': 20, 'rationale': 'x'}, headers=login(client, 'analyst@test'))
    assert r2.status_code == 409 and '冻结' in r2.json()['detail']
    # second approval is refused
    r3 = client.patch(f"/plans/{p['id']}/approval", json={'version': 2, 'status': 'rejected', 'review_note': '再退'}, headers=login(client, 'admin@test'))
    assert r3.status_code == 409
    # export reflects the approved v2 fields
    e = client.get(f"/plans/{p['id']}/export", headers=login(client, 'admin@test'))
    assert e.status_code == 200
    text = e.text
    assert 'v2 名称' in text and 'v2 理由' in text and '同意 v2' in text and 'v2' in text
    assert '草案' not in text


def test_stale_approval_cannot_approve_newer_version(client):
    p = seed_plan(client)
    client.patch(f"/plans/{p['id']}", json={'version': 1, 'name': 'v2', 'finding_ids': [2], 'target_slice': 'mobile', 'sample_target': 250, 'rationale': 'r2'}, headers=login(client, 'analyst@test'))
    r = client.patch(f"/plans/{p['id']}/approval", json={'version': 1, 'status': 'approved', 'review_note': '旧版同意'}, headers=login(client, 'admin@test'))
    assert r.status_code == 409
    assert '过期' in r.json()['detail'] and 'v2' in r.json()['detail']
    d = client.get(f"/plans/{p['id']}", headers=login(client, 'admin@test')).json()
    assert d['status'] == 'draft' and d['current_version'] == 2 and d['approved_version'] is None


def test_unknown_finding_rejected(client):
    p = seed_plan(client)
    r = client.patch(f"/plans/{p['id']}", json={'version': 1, 'name': 'x', 'finding_ids': [99], 'target_slice': 's', 'sample_target': 200, 'rationale': 'r'}, headers=login(client, 'analyst@test'))
    assert r.status_code == 422


def test_viewer_cannot_revise(client):
    p = seed_plan(client)
    with SessionLocal() as db:
        u = User(email='viewer@test', name='观察员', role='viewer', password_hash=hash_password('x'))
        db.add(u); db.commit()
    r = client.patch(f"/plans/{p['id']}", json={'version': 1, 'name': 'x', 'finding_ids': [1], 'target_slice': 's', 'sample_target': 200, 'rationale': 'r'}, headers=login(client, 'viewer@test'))
    assert r.status_code == 403


def test_legacy_approved_plan_backfilled_and_exportable(client):
    """升级前就已 approved、没有版本行的计划也应可导出，并补齐版本记录。"""
    with SessionLocal() as db:
        run = db.get(ComparisonRun, 1)
        legacy = ResamplingPlan(run_id=run.id, name='旧获批计划', finding_ids=[1], target_slice='legacy', sample_target=200, rationale='旧理由', status='approved', review_note='旧审稿', creator_id=1, current_version=1, approved_version=None)
        db.add(legacy); db.commit(); pid = legacy.id
    from src.services.plans import backfill_versions
    with SessionLocal() as db:
        backfill_versions(db)
    d = client.get(f'/plans/{pid}', headers=login(client, 'admin@test')).json()
    assert d['approved_version'] == 1 and d['effective_version'] == 1
    e = client.get(f'/plans/{pid}/export', headers=login(client, 'admin@test'))
    assert e.status_code == 200 and '旧获批计划' in e.text and '旧审稿' in e.text


def test_rejected_version_must_be_revised_before_reapproval(client):
    """退回是 v1 的最终决定；同一版本不能再批，修订后按新版本审核。"""
    p = seed_plan(client)
    client.patch(f"/plans/{p['id']}/approval", json={'version': 1, 'status': 'rejected', 'review_note': '先补来源'}, headers=login(client, 'admin@test'))
    r = client.patch(f"/plans/{p['id']}/approval", json={'version': 1, 'status': 'approved', 'review_note': '直接改判'}, headers=login(client, 'admin@test'))
    assert r.status_code == 409
    assert client.get(f"/plans/{p['id']}", headers=login(client, 'admin@test')).json()['status'] == 'rejected'
    rev = client.patch(f"/plans/{p['id']}", json={'version': 1, 'name': '已补来源', 'finding_ids': [1], 'target_slice': 'studio', 'sample_target': 200, 'rationale': '来源已核对'}, headers=login(client, 'analyst@test'))
    assert rev.status_code == 200 and rev.json()['status'] == 'draft' and rev.json()['current_version'] == 2
    d = client.patch(f"/plans/{p['id']}/approval", json={'version': 2, 'status': 'approved', 'review_note': '来源已补，同意'}, headers=login(client, 'admin@test')).json()
    assert d['status'] == 'approved' and d['approved_version'] == 2
    assert d['versions'][0]['review_note'] == '先补来源' and d['versions'][1]['review_note'] == '来源已补，同意'


def test_export_survives_plan_mirror_drift(client):
    """导出绑定获批版本与其发现快照，不被后续镜像字段改动影响。"""
    p = seed_plan(client)
    r = client.patch(f"/plans/{p['id']}/approval", json={'version': 1, 'status': 'approved', 'review_note': '同意'}, headers=login(client, 'admin@test'))
    assert r.status_code == 200
    with SessionLocal() as db:
        obj = db.get(ResamplingPlan, p['id'])
        obj.rationale = '被意外改写的镜像字段'
        db.commit()
    e = client.get(f"/plans/{p['id']}/export", headers=login(client, 'admin@test'))
    assert '理由' in e.text and '被意外改写的镜像字段' not in e.text
    assert 'v1' in e.text
