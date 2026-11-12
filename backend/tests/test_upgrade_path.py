"""Checks the upgrade path: an old-schema DB gains version columns, keeps approved exports."""
import os
import tempfile

os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('SEED_DEMO', 'false')
os.environ['REDIS_URL'] = 'redis://localhost:6379/0'
LEGACY_URL = 'sqlite:///' + tempfile.mkdtemp() + '/legacy.db'

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from src.models import ResamplingPlan
from src.constants.enums import PlanStatus


def _legacy_engine():
    """旧库只打补丁到版本列；版本表由正常启动的 create_all 建出。"""
    engine = create_engine(LEGACY_URL)
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE resampling_plans (
            id INTEGER PRIMARY KEY, run_id INTEGER, name VARCHAR(100), finding_ids TEXT,
            target_slice VARCHAR(160), sample_target INTEGER, rationale VARCHAR(500),
            status VARCHAR(20), review_note VARCHAR(500), creator_id INTEGER, created_at DATETIME)"""))
        c.execute(text("""CREATE TABLE comparison_runs (
            id INTEGER PRIMARY KEY, reference_id INTEGER, candidate_id INTEGER, name VARCHAR(100),
            config TEXT, status VARCHAR(20), result TEXT, error VARCHAR(1000), creator_id INTEGER, created_at DATETIME)"""))
        c.execute(text("INSERT INTO comparison_runs (id,name,status,config,result) VALUES (1,'旧运行','succeeded','{}','{}')"))
        c.execute(text("INSERT INTO resampling_plans (id,run_id,name,finding_ids,target_slice,sample_target,rationale,status,review_note,creator_id) VALUES (1,1,'旧草案','[1]','legacy',200,'旧理由','draft','',1)"))
        c.execute(text("INSERT INTO resampling_plans (id,run_id,name,finding_ids,target_slice,sample_target,rationale,status,review_note,creator_id) VALUES (2,1,'旧获批','[1]','legacy2',200,'旧理由2','approved','旧同意',1)"))
    return engine


def test_upgrade_adds_columns_and_backfills():
    from src.config.migrate import ensure_plan_schema
    from src.services.plans import backfill_versions
    from src.config.database import Base
    engine = _legacy_engine()
    Base.metadata.create_all(engine)  # 新版启动：新增 resampling_plan_versions 表
    ensure_plan_schema(engine)
    ensure_plan_schema(engine)  # 幂等 / 并发重入
    with engine.connect() as c:
        cols = {r[1] for r in c.execute(text('PRAGMA table_info(resampling_plans)'))}
        rows = c.execute(text('SELECT id,current_version,approved_version FROM resampling_plans ORDER BY id')).fetchall()
    assert {'current_version', 'approved_version', 'reviewer_id', 'reviewed_at', 'updated_at'} <= cols
    assert rows == [(1, 1, None), (2, 1, None)]

    S = sessionmaker(engine, expire_on_commit=False)
    with S() as db:
        backfill_versions(db)
    with S() as db:
        p1 = db.get(ResamplingPlan, 1)
        p2 = db.get(ResamplingPlan, 2)
        assert p1.current_version == 1 and p1.approved_version is None
        assert p2.status == PlanStatus.APPROVED and p2.approved_version == 1 and p2.current_version == 1
