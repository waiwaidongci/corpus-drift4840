from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy import select,update
from src.models import ResamplingPlan,ResamplingPlanVersion,ComparisonRun,DriftFinding,User
from src.utils.serialize import row
from src.constants.enums import PlanStatus,RunStatus
from .audit import record
CONTENT=('name','finding_ids','target_slice','sample_target','rationale')
def _now():return datetime.now(timezone.utc)
def _iso(t):return t.isoformat() if t else None
def _validate_findings(db,run_id,finding_ids):
 if not finding_ids:raise HTTPException(422,'至少关联一个分布发现')
 if len(set(finding_ids))!=len(finding_ids):raise HTTPException(422,'发现不得重复')
 valid=set(db.scalars(select(DriftFinding.id).where(DriftFinding.run_id==run_id)))
 if not set(finding_ids)<=valid:raise HTTPException(422,'发现必须属于本运行')
 return list(finding_ids)
def _names(db,ids):
 ids={i for i in ids if i};return {u.id:u.name for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}
def _versions(db,plan_id):
 return list(db.scalars(select(ResamplingPlanVersion).where(ResamplingPlanVersion.plan_id==plan_id).order_by(ResamplingPlanVersion.version)))
def _version_dict(v,names):
 return {'id':v.id,'plan_id':v.plan_id,'version':v.version,'name':v.name,'finding_ids':v.finding_ids,'target_slice':v.target_slice,'sample_target':v.sample_target,'rationale':v.rationale,'status':v.status,'review_note':v.review_note,'reviewer_id':v.reviewer_id,'reviewer_name':names.get(v.reviewer_id,''),'reviewed_at':_iso(v.reviewed_at),'created_by':v.created_by,'created_by_name':names.get(v.created_by,''),'created_at':_iso(v.created_at)}
def _effective(plan,versions):
 """获批计划展示/导出它所批准的那一版；其余展示当前版。"""
 if plan.status==PlanStatus.APPROVED and plan.approved_version:
  for v in versions:
   if v.version==plan.approved_version:return v
 for v in versions:
  if v.version==plan.current_version:return v
 return versions[-1] if versions else None
def _payload(db,plan):
 versions=_versions(db,plan.id)
 effective=_effective(plan,versions)
 names=_names(db,{plan.reviewer_id}|{v.created_by for v in versions}|{v.reviewer_id for v in versions}|({effective.reviewer_id} if effective else set()))
 data=row(plan)
 if effective:
  # 页面展示当前版；获批计划则固定展示被批准的那一版，避免镜像字段漂移。
  for f in CONTENT:data[f]=getattr(effective,f)
  data['review_note']=effective.review_note
  data['reviewer_id']=effective.reviewer_id
  data['reviewed_at']=_iso(effective.reviewed_at)
 data['reviewer_name']=names.get(data.get('reviewer_id'),'')
 data['effective_version']=effective.version if effective else plan.current_version
 data['versions']=[_version_dict(v,names) for v in versions]
 return data
def list_plans(db):return [_payload(db,p) for p in db.scalars(select(ResamplingPlan).order_by(ResamplingPlan.id.desc()))]
def get_plan(db,id):
 p=db.get(ResamplingPlan,id)
 if not p:raise HTTPException(404,'补采计划不存在')
 return _payload(db,p)
def create_plan(db,data,user):
 run=db.get(ComparisonRun,data.run_id)
 if not run or run.status!=RunStatus.SUCCEEDED:raise HTTPException(422,'请选择成功运行')
 content=data.model_dump(exclude={'run_id'});content['finding_ids']=_validate_findings(db,run.id,content['finding_ids'])
 obj=ResamplingPlan(**content,run_id=run.id,creator_id=user.id,status=PlanStatus.DRAFT,current_version=1,updated_at=_now())
 db.add(obj);db.flush()
 db.add(ResamplingPlanVersion(plan_id=obj.id,version=1,created_by=user.id,status=PlanStatus.DRAFT,**content))
 db.commit();record(db,user.email,'resampling.created',{'plan_id':obj.id,'version':1,'finding_ids':content['finding_ids']})
 return _payload(db,obj)
def _check_version(p,expected):
 if expected!=p.current_version:
  raise HTTPException(409,f'版本已过期：本次操作基于 v{expected}，计划当前为 v{p.current_version}；请刷新后重新基于最新版本操作')
def _stale(db,id,expected,action):
 """条件更新失败后给出明确原因；先回滚以便读到其它事务已提交的当前版本。"""
 db.rollback();now=db.get(ResamplingPlan,id)
 if not now:raise HTTPException(404,'补采计划不存在')
 _check_version(now,expected)
 if now.status==PlanStatus.APPROVED:raise HTTPException(409,f'v{now.current_version} 已获批并冻结，不能修改或再次审核')
 if action=='edit':raise HTTPException(409,f'v{now.current_version} 当前状态为 {now.status}，不可修订')
 raise HTTPException(409,f'v{now.current_version} 已被其它审核操作处理，请刷新后重试')
def revise_plan(db,id,data,user):
 p=db.get(ResamplingPlan,id)
 if not p:raise HTTPException(404,'补采计划不存在')
 _check_version(p,data.version)
 if p.status==PlanStatus.APPROVED:raise HTTPException(409,'获批版本已冻结，不能修订；请建立新的补采计划')
 expected=data.version
 ids=_validate_findings(db,p.run_id,data.finding_ids)
 content=data.model_dump(exclude={'version'});content['finding_ids']=ids
 res=db.execute(update(ResamplingPlan).where(ResamplingPlan.id==id,ResamplingPlan.current_version==expected,ResamplingPlan.status.in_((PlanStatus.DRAFT,PlanStatus.REJECTED))).values(**content,status=PlanStatus.DRAFT,review_note='',reviewer_id=None,reviewed_at=None,approved_version=None,current_version=expected+1,updated_at=_now()))
 if res.rowcount!=1:_stale(db,id,expected,'edit')
 new_version=expected+1
 db.add(ResamplingPlanVersion(plan_id=id,version=new_version,created_by=user.id,status=PlanStatus.DRAFT,**content))
 db.commit();record(db,user.email,'resampling.revised',{'plan_id':id,'from_version':expected,'to_version':new_version,'finding_ids':ids})
 db.expire_all()
 return _payload(db,db.get(ResamplingPlan,id))
def _finding_snapshot(db,finding_ids):
 items=[]
 for fid in finding_ids:
  f=db.get(DriftFinding,fid)
  if f:
   m=f.metrics;items.append({'id':f.id,'feature':f.feature,'psi':m.get('psi'),'js':m.get('js'),'p_value':m.get('p_value'),'q_value':m.get('q_value'),'fdr_reject':m.get('fdr_reject')})
 return items
def approve_plan(db,id,data,user):
 p=db.get(ResamplingPlan,id)
 if not p:raise HTTPException(404,'补采计划不存在')
 _check_version(p,data.version)
 if p.status==PlanStatus.APPROVED:raise HTTPException(409,'获批版本已冻结，不能再次审核')
 if p.status!=PlanStatus.DRAFT:raise HTTPException(409,f'v{p.current_version} 当前状态为 {p.status}，不是待审草案；请刷新后按最新状态操作')
 expected=data.version
 now=_now()
 res=db.execute(update(ResamplingPlan).where(ResamplingPlan.id==id,ResamplingPlan.current_version==expected,ResamplingPlan.status==PlanStatus.DRAFT).values(status=data.status,review_note=data.review_note,reviewer_id=user.id,reviewed_at=now,approved_version=(expected if data.status==PlanStatus.APPROVED else None),updated_at=now))
 if res.rowcount!=1:_stale(db,id,expected,'review')
 v=db.scalar(select(ResamplingPlanVersion).where(ResamplingPlanVersion.plan_id==id,ResamplingPlanVersion.version==expected))
 if v:
  v.status=data.status;v.review_note=data.review_note;v.reviewer_id=user.id;v.reviewed_at=now
  if data.status==PlanStatus.APPROVED:v.finding_snapshot=_finding_snapshot(db,v.finding_ids)
 db.commit();record(db,user.email,'resampling.reviewed',{'plan_id':id,'version':expected,'status':data.status})
 db.expire_all()
 return _payload(db,db.get(ResamplingPlan,id))
def export_content(db,id):
 """返回实际获批版本的内容，而不是可变的镜像字段。"""
 p=db.get(ResamplingPlan,id)
 if not p:raise HTTPException(404,'计划不存在')
 if p.status!=PlanStatus.APPROVED:raise HTTPException(403,'仅获批计划可导出')
 versions=_versions(db,id)
 v=_effective(p,versions)
 if v:
  return {'version':v.version,'name':v.name,'finding_ids':v.finding_ids,'target_slice':v.target_slice,'sample_target':v.sample_target,'rationale':v.rationale,'review_note':v.review_note,'finding_snapshot':v.finding_snapshot}
 # 兼容升级前已获批、尚无版本行的计划：按获批镜像导出。
 return {'version':p.current_version or 1,'name':p.name,'finding_ids':p.finding_ids,'target_slice':p.target_slice,'sample_target':p.sample_target,'rationale':p.rationale,'review_note':p.review_note,'finding_snapshot':None}
def add_initial_version(db,plan):
 db.flush()
 db.add(ResamplingPlanVersion(plan_id=plan.id,version=plan.current_version or 1,name=plan.name,finding_ids=plan.finding_ids,target_slice=plan.target_slice,sample_target=plan.sample_target,rationale=plan.rationale,status=plan.status,review_note=plan.review_note or '',reviewer_id=plan.reviewer_id,reviewed_at=plan.reviewed_at,created_by=plan.creator_id))
def backfill_versions(db):
 """为旧库中已存在的计划补齐版本记录，获批旧计划仍可导出。"""
 changed=False
 for p in db.scalars(select(ResamplingPlan)):
  versions=_versions(db,p.id)
  if not versions:
   vno=p.current_version or 1
   status=p.status or PlanStatus.DRAFT
   v=ResamplingPlanVersion(plan_id=p.id,version=vno,name=p.name,finding_ids=p.finding_ids,target_slice=p.target_slice,sample_target=p.sample_target,rationale=p.rationale,status=status,review_note=p.review_note or '',reviewer_id=p.reviewer_id,reviewed_at=p.reviewed_at,created_by=p.creator_id)
   db.add(v);p.current_version=vno
   if status==PlanStatus.APPROVED:p.approved_version=vno
   changed=True
  else:
   if not p.current_version:
    p.current_version=max(v.version for v in versions);changed=True
   if p.status==PlanStatus.APPROVED and not p.approved_version:
    p.approved_version=next((v.version for v in versions if v.status==PlanStatus.APPROVED),p.current_version);changed=True
  if p.updated_at is None:
   p.updated_at=p.created_at or _now();changed=True
 if changed:db.commit()
