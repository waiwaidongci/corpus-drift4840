import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text
from redis import Redis
from src.config.database import Base,engine,SessionLocal
from src.config.settings import SEED_DEMO,REDIS_URL
from src.models import *
from src.middlewares.request_id import RequestIDMiddleware
from src.middlewares.errors import ErrorMiddleware
from src.routes import auth,snapshots,runs,plans,audit
@asynccontextmanager
async def lifespan(app):
 Base.metadata.create_all(engine)
 try:
  from src.config.migrate import ensure_plan_schema
  ensure_plan_schema(engine)
  from src.services.plans import backfill_versions
  with SessionLocal() as db:backfill_versions(db)
 except Exception:logging.exception('Plan schema/version upgrade failed; startup continues')
 if SEED_DEMO:
  try:
   from scripts.seed_demo import seed
   seed()
  except Exception:logging.exception('Seed failure; startup continues')
 yield
app=FastAPI(title='言屿语料分布校对室',lifespan=lifespan)
app.add_middleware(ErrorMiddleware);app.add_middleware(RequestIDMiddleware)
for route in (auth,snapshots,runs,plans,audit):app.include_router(route.router)
@app.get('/health')
def health():
 with engine.connect() as c:c.execute(text('select 1'))
 Redis.from_url(REDIS_URL).ping();return {'status':'ok'}
