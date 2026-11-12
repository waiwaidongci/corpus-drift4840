import logging
from sqlalchemy import inspect,text
from sqlalchemy.exc import OperationalError
COLUMNS={
 'current_version':'INTEGER NOT NULL DEFAULT 1',
 'approved_version':'INTEGER NULL',
 'reviewer_id':'INTEGER NULL',
 'reviewed_at':'DATETIME NULL',
 'updated_at':'DATETIME NULL',
}
def ensure_plan_schema(engine):
 """metadata.create_all 只建新表，不 ALTER 旧表；为升级前的库补上版本列。

 逐列补加并容忍并发启动的另一实例已抢先添加同一列。
 """
 applied=[]
 with engine.begin() as conn:
  existing={c['name'] for c in inspect(conn).get_columns('resampling_plans')}
  for name,ddl in COLUMNS.items():
   if name in existing:continue
   try:
    conn.execute(text(f'ALTER TABLE resampling_plans ADD COLUMN {name} {ddl}'))
    applied.append(name)
   except OperationalError as e:
    if 'duplicate column' not in str(e).lower():raise
  conn.execute(text('UPDATE resampling_plans SET current_version=1 WHERE current_version IS NULL'))
 if applied:logging.info('plan_schema_upgraded columns=%s',applied)
