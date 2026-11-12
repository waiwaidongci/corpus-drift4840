<script setup lang="ts">
import {computed,reactive,ref} from 'vue'
import type {Plan,PlanVersion,User} from '../types/domain'
import {researchApi} from '../api/research'
import {useAsync} from '../hooks/useAsync'
import {date} from '../utils/format'
import PageTitle from '../components/common/PageTitle.vue'
import Badge from '../components/common/Badge.vue'
import Notice from '../components/common/Notice.vue'
import Empty from '../components/common/Empty.vue'

const props=defineProps<{plans:Plan[];user:User;reload:()=>Promise<any>}>()
const note=ref('先核对采样口径，再按切片补齐；后续使用独立质量评测验证效果。')
const {task,act}=useAsync()
const editing=ref<number|null>(null)
const drafts=reactive<Record<number,any>>({})
const historyOpen=reactive<Record<number,boolean>>({})
const canWrite=computed(()=>props.user.role!=='viewer')
const parseIds=(text:string)=>text.split(/[,，\s]+/).filter(Boolean).map(Number).filter(n=>Number.isInteger(n)&&n>0)
function beginEdit(p:Plan){
 editing.value=p.id
 drafts[p.id]={version:p.current_version,name:p.name,findingText:p.finding_ids.join(', '),target_slice:p.target_slice,sample_target:p.sample_target,rationale:p.rationale}
}
function cancelEdit(){editing.value=null}
function versionLabel(p:Plan,v:PlanVersion){
 const marks=[]
 if(v.version===p.current_version)marks.push('当前版')
 if(p.status==='approved'&&v.version===p.effective_version)marks.push('获批版')
 return marks.length?' · '+marks.join(' / '):''
}
async function save(p:Plan){
 const d=drafts[p.id]
 const finding_ids=parseIds(d.findingText)
 if(!finding_ids.length){task.error='请填写至少一个关联分布发现 ID';task.message='';return}
 await act(async()=>{
  await researchApi.revise(p.id,{version:d.version,name:d.name,finding_ids,target_slice:d.target_slice,sample_target:d.sample_target,rationale:d.rationale})
  editing.value=null
  await props.reload()
 },'已保存为新草案版本 v'+(d.version+1)+'，等待负责人重新审核')
}
async function review(p:Plan,status:'approved'|'rejected'){
 await act(async()=>{
  await researchApi.approve(p.id,p.current_version,status,note.value)
  await props.reload()
 },status==='approved'?'补采计划 v'+p.current_version+' 已确认':'计划 v'+p.current_version+' 已退回修改')
}
function exportPlan(p:Plan){
 return act(()=>researchApi.export(p.id,p.effective_version),'获批补采 v'+p.effective_version+' CSV 已导出')
}
</script>
<template>
<PageTitle index="05 / RESAMPLING DESK" title="补采之前，先把理由写清楚。" description="草案与退回计划均可修订，并保留各版内容、关联发现与审核意见。改稿和审核只接受基于当前版本的操作，过期操作会被明确拒绝，旧版同意不能批准新版；获批版本冻结，页面与导出都以批准的那一版为准。"/>
<Notice :error="task.error" :message="task.message"/>
<label class="approval-note" v-if="user.role==='admin'">本次审稿说明<input v-model="note" required maxlength="500"/></label>
<Empty v-if="!plans.length" title="等待第一份补采草案" help="在漂移发现页选择一个特征，并写明补采切片和依据。"/>
<div class="plan-list">
 <article v-for="p in plans" :key="p.id">
  <header>
   <span class="proof-number">{{String(p.id).padStart(2,'0')}}</span>
   <div>
    <small>PROOF / {{p.run_id}} · FINDINGS {{p.finding_ids.join(', ')}}</small>
    <h2>{{p.name}}</h2>
    <div class="version-line">
     <span>当前 v{{p.current_version}}</span>
     <span v-if="p.status==='approved'">获批 v{{p.effective_version}}</span>
     <span v-if="p.reviewer_name">审核 {{p.reviewer_name}}</span>
     <span>更新 {{p.updated_at?date(p.updated_at):'—'}}</span>
    </div>
   </div>
   <Badge :status="p.status"/>
  </header>
  <div class="plan-body">
   <dl>
    <dt>目标切片</dt><dd>{{p.target_slice}}</dd>
    <dt>目标片段</dt><dd>{{p.sample_target}}</dd>
    <dt>关联发现</dt><dd>{{p.finding_ids.join(', ')}}</dd>
   </dl>
   <section>
    <small>研究员依据</small>
    <p>{{p.rationale}}</p>
    <small v-if="p.review_note">负责人说明 · v{{p.effective_version}}</small>
    <p v-if="p.review_note">{{p.review_note}}</p>
   </section>
  </div>

  <form v-if="editing===p.id&&canWrite" class="editor plan-revision" @submit.prevent="save(p)">
   <small>REVISE / 基于 v{{drafts[p.id].version}} 修订</small>
   <h2>{{p.status==='rejected'?'修改退回的草案':'修订草案'}}，保存为新版本</h2>
   <div class="form-grid">
    <label>计划名称<input v-model="drafts[p.id].name" required maxlength="100"/></label>
    <label>目标切片<input v-model="drafts[p.id].target_slice" required maxlength="160"/></label>
    <label>目标片段数<input type="number" v-model.number="drafts[p.id].sample_target" min="20" max="10000" step="1" required/></label>
   </div>
   <label>关联分布发现（ID，逗号分隔）<input v-model="drafts[p.id].findingText" placeholder="例如 3, 5" required/></label>
   <label>补采依据<textarea rows="3" v-model="drafts[p.id].rationale" required maxlength="500"/></label>
   <p class="fine">保存会生成 v{{drafts[p.id].version+1}}；原版本内容、关联发现与审核意见继续保留。若计划曾被退回，保存后回到待审稿并需重新审核。</p>
   <div class="plan-actions">
    <button type="button" @click="cancelEdit">取消</button>
    <button class="primary" :disabled="task.busy">保存 v{{drafts[p.id].version+1}} →</button>
   </div>
  </form>

  <div class="plan-actions">
   <button :disabled="p.status!=='approved'||task.busy" @click="exportPlan(p)">导出获批 v{{p.effective_version}} CSV ↗</button>
   <button v-if="canWrite&&p.status!=='approved'" :disabled="task.busy||editing===p.id" @click="beginEdit(p)">修订为 v{{p.current_version+1}} →</button>
   <template v-if="user.role==='admin'&&p.status==='draft'">
    <button :disabled="task.busy" @click="review(p,'rejected')">退回修改</button>
    <button class="primary" :disabled="task.busy" @click="review(p,'approved')">确认 v{{p.current_version}} 补采说明 →</button>
   </template>
   <button class="text-button" @click="historyOpen[p.id]=!historyOpen[p.id]">{{historyOpen[p.id]?'收起版本记录':'查看版本记录（'+p.versions.length+'）'}} ↗</button>
  </div>

  <section v-if="historyOpen[p.id]" class="version-history">
   <div class="section-title"><h2>版本记录</h2><small>每次改稿或审核生成一条不可变记录</small></div>
   <div class="version-item" v-for="v in [...p.versions].reverse()" :key="v.id">
    <header>
     <strong>v{{v.version}}</strong>
     <Badge :status="v.status"/>
     <small>{{versionLabel(p,v)}}</small>
    </header>
    <dl>
     <dt>名称</dt><dd>{{v.name}}</dd>
     <dt>切片 / 目标</dt><dd>{{v.target_slice}} · {{v.sample_target}} 片段</dd>
     <dt>关联发现</dt><dd>{{v.finding_ids.join(', ')}}</dd>
     <dt>依据</dt><dd>{{v.rationale}}</dd>
     <dt v-if="v.review_note">审核意见</dt><dd v-if="v.review_note">{{v.review_note}}</dd>
    </dl>
    <small class="version-meta">{{v.created_by_name||'—'}} 保存于 {{date(v.created_at)}}<span v-if="v.reviewer_name"> · {{v.reviewer_name}} 审核于 {{v.reviewed_at?date(v.reviewed_at):'—'}}</span></small>
   </div>
  </section>
 </article>
</div>
</template>
<style scoped>
.version-line{display:flex;gap:16px;flex-wrap:wrap;margin-top:8px;font-size:9px;color:var(--muted);letter-spacing:0}
.plan-revision{margin:24px 0 0;border-color:var(--brick)}
.version-history{margin-top:22px;border-top:1px solid var(--line);padding-top:18px}
.version-item{border-bottom:1px dashed var(--line);padding:14px 0}
.version-item>header{display:flex;gap:14px;align-items:center}
.version-item>header small{color:var(--muted);letter-spacing:0}
.version-item dl{display:grid;grid-template-columns:110px minmax(0,1fr);gap:6px 16px;margin:10px 0;font-size:11px;line-height:1.8}
.version-item dt{color:var(--muted)}
.version-item dd{margin:0;overflow-wrap:anywhere}
.version-meta{color:var(--muted);letter-spacing:0}
</style>
