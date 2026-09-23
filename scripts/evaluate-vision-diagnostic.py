"""Exploratory follow-up after baseline: same production criterion grader in both image arms.
Retains the original failed scalar-score diagnostic unchanged. Shares campaign budget.
"""
raise SystemExit("Archived diagnostic: it tested question charts, not uploaded learner answers. Use scripts/evaluate-answer-images.py. Historical results are preserved.")
import copy,json,sys,hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pen.practice.importer import parse_handbook
from pen.practice.grading import grade_answer
from pen.practice.evaluation import Recorder
from pen.config import LLMConfig,parse_dotenv
base=ROOT/'evals/mq_v1';out=base/'runs/vision-criteria-followup';out.mkdir(exist_ok=True)
manifest=json.loads((base/'manifest.json').read_text())
qs={q['title'].split(' · ')[0]:q for q in parse_handbook(base/'book.md')['questions']}
visual=json.loads((base/'visual.json').read_text())
cfg=LLMConfig('https://api.deepseek.com',parse_dotenv(Path.home()/'dev/Socrates-agent/.env')['DEEPSEEK_API_KEY'],'deepseek-flash','evaluation',thinking='off',vision=True,provider='deepseek')
rec=Recorder(out,cfg)
(out/'manifest.json').write_text(json.dumps({'exploratory':True,'reason':'Control output schema while withholding concrete reference answers; both arms use production criterion grader. Not a replacement of formal-v1.', 'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'dataset_sha256':hashlib.sha256((base/'manifest.json').read_bytes()).hexdigest()},ensure_ascii=False,indent=2))

def run(t):
    v,image,kind,rep=t;cid=v['case_id'];qid=f'{cid}-{image}-{kind}-r{rep}'
    q=copy.deepcopy(qs[cid]);q['type']='short_answer'
    q['reference_answer']='必须依据实际提供的图像独立核对，参考方法不提供任何具体数值或正确选项。'
    q['solution_markdown']=''
    if not image:q['assets']=[]
    defs=['根据图像判断学生选择的选项是否正确。'] if qs[cid]['type']=='single_choice' else ['核对学生写出的A和B数值是否同时等于图中的标注值。','核对学生写出的B减A是否等于图中真实B值减A值。'] if cid.startswith('U5') else ['核对学生给出的算术平均数是否等于图中两列真实值之和除以二。','核对学生指出的高于平均数的列是否确实高于按图中真实数值计算的平均数。']
    q['criteria']=[{'id':f'c{i+1}','point_id':f'p{i+1}','max_score':1/len(defs),'description':d,'partial_credit':'准确完成本项得本项满分；错误、缺失或矛盾为0；不要用学生答案中的数值代替图中真实值。'} for i,d in enumerate(defs)]
    row={'id':qid,'case_id':cid,'with_image':image,'answer_kind':kind,'repeat':rep,'expected':1 if kind=='full' else 0,'ok':False}
    latest={}
    def call(system,payload):
        response=rec.call(qid,system+'\nIf the required image is absent, return JSON {"status":"insufficient_evidence"} instead of guessing a grade.',payload)
        latest.update(response);return response
    try:
        grade=grade_answer(q,{'text':v['expected_answer'] if kind=='full' else v['wrong_answer']},call)
        row.update(ok=True,grade=grade,score=grade['score']/grade['max_score'])
    except Exception as e:
        row['error']=str(e);row['abstained']=latest.get('status')=='insufficient_evidence'
    (out/'results').mkdir(exist_ok=True)
    (out/'results'/f'{qid}.json').write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n')
    return row

tasks=[(v,img,kind,r) for v in visual if manifest['split'][v['case_id']]=='test' for img in [True,False] for kind in ['full','wrong'] for r in range(3)]
with ThreadPoolExecutor(max_workers=6) as pool:
    results=list(pool.map(run,tasks))
summary={}
for img in [True,False]:
    group=[r for r in results if r['with_image']==img];good=[r for r in group if r['ok']]
    summary['image' if img else 'no_image']={'n':len(group),'valid_grades':len(good),'abstentions':sum(r.get('abstained',False) for r in group),'mae':sum(abs(r['score']-r['expected']) for r in good)/len(good) if good else None,'exact':sum(r['score']==r['expected'] for r in good),'over_credit':sum(r['score']>r['expected'] for r in good)}
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2),flush=True)
