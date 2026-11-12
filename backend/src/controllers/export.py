from fastapi import HTTPException
from src.models import DriftFinding
from src.services.plans import export_content
from src.utils.csv_export import csv_response
def export_plan(db,id):
 content=export_content(db,id)
 snapshot=content.get('finding_snapshot') or []
 if not snapshot:
  snapshot=[]
  for fid in content['finding_ids']:
   f=db.get(DriftFinding,fid)
   if not f:continue
   m=f.metrics
   snapshot.append({'id':f.id,'feature':f.feature,'psi':m.get('psi'),'js':m.get('js'),'p_value':m.get('p_value'),'q_value':m.get('q_value'),'fdr_reject':m.get('fdr_reject')})
 rows=[]
 for f in snapshot:
  rows.append([content['name'],f'v{content["version"]}',content['target_slice'],content['sample_target'],content['rationale'],content['review_note'],f.get('feature'),f.get('psi'),f.get('js'),f.get('p_value'),f.get('q_value'),f.get('fdr_reject')])
 return csv_response(f'resampling-{id}-v{content["version"]}.csv',rows,['计划','获批版本','补采切片','目标片段数','依据','审核说明','特征','PSI自然log','JS log2','原p','BH q','FDR拒绝'])
