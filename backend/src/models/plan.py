from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import String,JSON,ForeignKey,DateTime,Integer,UniqueConstraint
from datetime import datetime,timezone
from src.config.database import Base
class ResamplingPlan(Base):
 """Aggregate root. The mutable mirror fields always describe current_version;
 approved_version names the immutable version that was actually approved."""
 __tablename__='resampling_plans'
 id:Mapped[int]=mapped_column(primary_key=True)
 run_id:Mapped[int]=mapped_column(ForeignKey('comparison_runs.id'))
 name:Mapped[str]=mapped_column(String(100))
 finding_ids:Mapped[list]=mapped_column(JSON)
 target_slice:Mapped[str]=mapped_column(String(160))
 sample_target:Mapped[int]=mapped_column()
 rationale:Mapped[str]=mapped_column(String(500))
 status:Mapped[str]=mapped_column(String(20),default='draft')
 review_note:Mapped[str]=mapped_column(String(500),default='')
 creator_id:Mapped[int]=mapped_column(ForeignKey('users.id'))
 created_at:Mapped[datetime]=mapped_column(DateTime,default=lambda:datetime.now(timezone.utc))
 current_version:Mapped[int]=mapped_column(Integer,default=1,server_default='1')
 approved_version:Mapped[int|None]=mapped_column(Integer,nullable=True)
 reviewer_id:Mapped[int|None]=mapped_column(ForeignKey('users.id'),nullable=True)
 reviewed_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True)
 updated_at:Mapped[datetime]=mapped_column(DateTime,default=lambda:datetime.now(timezone.utc),onupdate=lambda:datetime.now(timezone.utc))
class ResamplingPlanVersion(Base):
 """Append-only snapshot of one plan revision or review decision."""
 __tablename__='resampling_plan_versions'
 __table_args__=(UniqueConstraint('plan_id','version'),)
 id:Mapped[int]=mapped_column(primary_key=True)
 plan_id:Mapped[int]=mapped_column(ForeignKey('resampling_plans.id'))
 version:Mapped[int]=mapped_column(Integer)
 name:Mapped[str]=mapped_column(String(100))
 finding_ids:Mapped[list]=mapped_column(JSON)
 target_slice:Mapped[str]=mapped_column(String(160))
 sample_target:Mapped[int]=mapped_column()
 rationale:Mapped[str]=mapped_column(String(500))
 status:Mapped[str]=mapped_column(String(20),default='draft')
 review_note:Mapped[str]=mapped_column(String(500),default='')
 finding_snapshot:Mapped[list|None]=mapped_column(JSON,nullable=True)
 reviewer_id:Mapped[int|None]=mapped_column(ForeignKey('users.id'),nullable=True)
 reviewed_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True)
 created_by:Mapped[int]=mapped_column(ForeignKey('users.id'))
 created_at:Mapped[datetime]=mapped_column(DateTime,default=lambda:datetime.now(timezone.utc))
