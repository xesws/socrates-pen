"""Frozen, bounded batch evaluation of an independently authored MQ textbook.

Phases: freeze/check -> rubric (dev then test) -> grade -> persist -> report.
Uses production parsing, rubric derivation, grading and coordinator persistence.
No user vault or existing evaluation campaign is modified.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pen.config import LLMConfig, parse_dotenv
from pen.practice.contracts import fingerprint
from pen.practice.evaluation import Recorder
from pen.practice.grading import grade_answer
from pen.practice.importer import derive_rubric, headings, parse_handbook

CODE = ['pen/practice/importer.py', 'pen/practice/grading.py', 'pen/practice/rubric.py',
        'pen/practice/rubric_examples.py', 'pen/practice/coordinator.py', 'pen/practice/resources.py',
        'pen/practice/llm.py', 'pen/practice/evaluation.py', 'scripts/evaluate-textbook.py']
SOURCE_FIELDS = ('title', 'type', 'prompt', 'reference_answer', 'solution_markdown')


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def cid(question):
    match = re.match(r'RL\d{2}Q\d{2}\b', question['title'])
    if not match:
        raise ValueError('Missing stable RL chapter/question ID: ' + question['title'])
    return match.group(0)


def expected_questions(fixture):
    raw = json.loads((fixture / 'expected.json').read_text())
    return raw['questions'] if isinstance(raw, dict) else raw


def check(fixture):
    text = (fixture / 'book.md').read_text()
    expected = expected_questions(fixture)
    bank = parse_handbook(fixture / 'book.md')
    issues = []
    if not 1000 <= len(text.splitlines()) <= 2000:
        issues.append('Textbook is outside the 1000–2000 physical-line range')
    if len(bank['units']) != 8 or len(bank['questions']) != 80 or len(expected) != 80 or bank['issues']:
        issues.append('Expected eight valid chapters and eighty questions: ' + str(bank['issues']))
    expected_by_id = {cid(q): q for q in expected}
    imported_by_id = {cid(q): q for q in bank['questions']}
    if len(expected_by_id) != 80 or set(imported_by_id) != set(expected_by_id):
        issues.append('Missing, extra, or duplicated question IDs')
    comparisons = []
    for ident, source in expected_by_id.items():
        q = imported_by_id.get(ident)
        mismatches = []
        if q is None:
            mismatches.append('missing question')
        else:
            for field in SOURCE_FIELDS:
                if q[field] != source[field]:
                    mismatches.append(field)
            if q['type'] == 'single_choice':
                for field in ('choices', 'correct_choice_id'):
                    if q[field] != source[field]:
                        mismatches.append(field)
            if q['type'] == 'fill_blank':
                # Importer adds display labels/default mode, but must preserve every
                # authored answer/alias/tolerance. Do not synthesize oracle via importer.
                expected_blanks = source['blanks']
                if isinstance(expected_blanks, dict):
                    expected_blanks = [{'id': k, **v} for k, v in expected_blanks.items()]
                actual = {b['id']: b for b in q['blanks']}
                if set(actual) != {b['id'] for b in expected_blanks}:
                    mismatches.append('blank IDs')
                for blank in expected_blanks:
                    if any(actual.get(blank['id'], {}).get(k) != v for k, v in blank.items()):
                        mismatches.append('blank ' + blank['id'])
            span = q['source']
            actual_source = '\n'.join(text.splitlines()[span['start_line']-1:span['end_line']]).strip('\n')
            if actual_source != q['raw_markdown']:
                mismatches.append('source span / raw Markdown')
        comparisons.append({'id': ident, 'ok': not mismatches, 'mismatches': mismatches})
    for chapter in range(1, 9):
        group = [q for q in bank['questions'] if cid(q).startswith(f'RL{chapter:02}')]
        counts = {kind: sum(q['type'] == kind for q in group) for kind in ('single_choice','fill_blank','short_answer')}
        if counts != {'single_choice':4,'fill_blank':3,'short_answer':3}:
            issues.append(f'Chapter {chapter} has incorrect question mix: {counts}')
    if not all(row['ok'] for row in comparisons):
        issues.append('Source fields differ from independent author manifest')
    result = {'ok': not issues, 'lines':len(text.splitlines()), 'chapters':len(bank['units']),
              'questions':len(bank['questions']), 'issues':issues, 'comparisons':comparisons}
    write(fixture / 'import-check.json', result)
    if issues:
        raise ValueError(json.dumps(result, ensure_ascii=False))
    write(fixture / 'imported-bank.json', bank)
    return bank


def freeze(fixture):
    if (fixture / 'manifest.json').exists():
        raise ValueError('Dataset already frozen; use a new dataset for revisions')
    check(fixture)
    review = json.loads((fixture / 'source-review.json').read_text())
    rows = review['questions']
    known = {cid(q) for q in expected_questions(fixture)}
    if {r['id'] for r in rows} != known or len(rows) != 80 or any(r['status'] != 'approved' for r in rows):
        raise ValueError('Primary-agent review must approve every source question before freezing')
    probes = json.loads((fixture / 'probe-answers.json').read_text())
    selected = {f'RL{ch:02}Q{q:02}' for ch in range(1,9) for q in (8,10)}
    kinds = {'full','paraphrase','basic','advanced','wrong','contradiction'}
    if len(probes)!=96 or {(p['case_id'],p['kind']) for p in probes} != {(q,k) for q in selected for k in kinds}:
        raise ValueError('Expected six prewritten answer types for sixteen selected questions')
    names = ['book.md','expected.json','source-review.json','probe-answers.json']
    write(fixture / 'manifest.json', {
        'author_model':'gpt-6-luna','reviewer':'primary-agent, not independent human annotation',
        'files':{name:hashlib.sha256((fixture/name).read_bytes()).hexdigest() for name in names},
        'split':{ident:('dev' if ident.startswith('RL01') else 'test') for ident in sorted(known)},
        'max_calls':240,'max_tokens':6_000_000,'model':'deepseek-flash','max_output_tokens':4096,
        'acceptance':{'extracted_questions':80,'valid_rubrics':80,'unresolved_substantive_review_issues':0,
                      'mae':.1,'within_10_points_fraction':.9,'wrong_answer_score':0},
    })


def frozen(fixture):
    manifest = json.loads((fixture/'manifest.json').read_text())
    for name, digest in manifest['files'].items():
        if hashlib.sha256((fixture/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Frozen source changed: '+name)
    return manifest


def prepare_run(fixture, directory, manifest):
    signature = {'dataset':fingerprint(manifest),'code':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in CODE},
                 'model':manifest['model'],'temperature':0,'thinking':'off','max_output_tokens':4096}
    path = directory/'manifest.json'
    if path.exists() and json.loads(path.read_text())!=signature:
        raise ValueError('Run inputs/code changed; use a new run name, keep previous results')
    write(path,signature)
    for name in CODE:
        target=directory/'code'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/name).read_bytes())


def run_paid(args):
    fixture=args.fixture; manifest=frozen(fixture)
    directory=fixture/'runs'/args.run
    prepare_run(fixture,directory,manifest)
    key=parse_dotenv(args.key_file).get('DEEPSEEK_API_KEY')
    if not key: raise ValueError('DEEPSEEK_API_KEY missing in explicit key file')
    cfg=LLMConfig('https://api.deepseek.com',key,manifest['model'],'evaluation',thinking='off',vision=False,provider='deepseek')
    recorder=Recorder(directory,cfg,max_calls=manifest['max_calls'],max_tokens=manifest['max_tokens'],max_output_tokens=4096)
    bank={cid(q):q for q in parse_handbook(fixture/'book.md')['questions']}
    selected={ident for ident in bank if args.split=='all' or manifest['split'][ident]==args.split}
    if args.cases: selected &= set(args.cases.split(','))
    if not selected: raise ValueError('No cases selected')
    def rubric(ident):
        row={'id':ident,'kind':'rubric','split':manifest['split'][ident],'ok':False}
        try:
            call=recorder.for_case('rubric-'+ident)
            if args.reuse_rubrics_from:
                source=fixture/'runs'/args.reuse_rubrics_from
                prior=source/'rubrics'/f'{ident}.json'
                if prior.is_file() and json.loads(prior.read_text()).get('ok'):
                    previous=json.loads(prior.read_text())
                    origin=fixture/'runs'/previous.get('call_run',args.reuse_rubrics_from)
                    records=[json.loads(p.read_text()) for p in sorted((origin/'calls').glob(f'rubric-{ident}--*.json'))]
                    saved=next((r for r in records if r['ok']),None)
                    def replay(system,payload):
                        signature=fingerprint([fingerprint([cfg.model,cfg.base_url,system,payload]),4096])
                        if saved is None or saved['signature']!=signature:
                            raise ValueError('Inherited rubric request changed: '+ident)
                        return copy.deepcopy(saved['parsed'])
                    call=replay
                    row['call_run']=origin.name
            part=derive_rubric(bank[ident],call)
            row.update(ok=True,part=part)
        except Exception as exc: row['error']=str(exc)
        write(directory/'rubrics'/f'{ident}.json',row)
        print(f'rubric {ident}: {"valid" if row["ok"] else row["error"]}',flush=True)
    def grade(sample):
        ident=sample['case_id']; name=ident+'-'+sample['kind']
        row={'id':name,'case_id':ident,'kind':sample['kind'],'split':manifest['split'][ident],
             'expected':sample['score'],'expected_criteria':sample.get('criterion_scores'), 'ok':False}
        try:
            derived=json.loads((directory/'rubrics'/f'{ident}.json').read_text())
            if not derived['ok']: raise ValueError('Rubric failed; answer was not graded')
            question=derived['part']['questions'][0]
            result=grade_answer(question,{'text':sample['text']},recorder.for_case('grade-'+name))
            row.update(ok=True,grade=result,score=result['score']/result['max_score'])
        except Exception as exc: row.update(error=str(exc),error_code=getattr(exc,'code','evaluation_failed'))
        write(directory/'grades'/f'{name}.json',row)
        print(f'grade {name}: {row.get("score",row.get("error"))} expected {row["expected"]}',flush=True)
    items=sorted(selected) if args.phase=='rubric' else [s for s in json.loads((fixture/'probe-answers.json').read_text()) if s['case_id'] in selected]
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        list(executor.map(rubric if args.phase=='rubric' else grade,items))


def persist(args):
    """Replay recorded real-model responses through the production build job.

    This verifies orchestration/storage without paying for duplicate model calls.
    Any request mismatch or missing rubric fails, never falls back to a live call.
    """
    frozen(args.fixture)
    from pen import config
    from pen.practice import coordinator as core
    from pen.practice.runtime import set_enabled
    from pen.practice.store import Store
    directory=args.fixture/'runs'/args.run
    bank=parse_handbook(args.fixture/'book.md')
    by_prompt={q['prompt']:cid(q) for q in bank['questions']}
    def replay(system,payload):
        ident=by_prompt[payload['prompt']]
        result=json.loads((directory/'rubrics'/f'{ident}.json').read_text())
        origin=args.fixture/'runs'/result.get('call_run',args.run)
        paths=sorted((origin/'calls').glob(f'rubric-{ident}--*.json'))
        row=next((json.loads(p.read_text()) for p in paths if json.loads(p.read_text()).get('ok')),None)
        signature=fingerprint([fingerprint(['deepseek-flash','https://api.deepseek.com',system,payload]),4096])
        if row is None or row['signature']!=signature:
            raise ValueError('Missing or changed recorded rubric: '+ident)
        return copy.deepcopy(row['parsed'])
    old_home,old_llm,old_launch=config.PEN_DIR,core._llm,core.launch
    try:
        with tempfile.TemporaryDirectory(prefix='socrates-rl-eval-') as temporary:
            config.PEN_DIR=Path(temporary)
            core._llm=lambda *a:replay
            core.launch=lambda key,fn,*a:fn(*a)
            scope,hid='rl-eval-isolated','rl-textbook-v1'
            set_enabled(scope,True)
            job=core.start_build(scope,hid,args.fixture/'book.md',object())
            stored_job=Store().get(scope,'job',job['id'])
            resource=core.active_resource(scope,hid)
            if not resource or stored_job['status']!='completed' or len(resource['questions'])!=80:
                write(directory/'persistence.json',{'ok':False,'job':core.public_job(stored_job)})
                raise ValueError('Production bank build did not publish all 80 questions')
            generated={q['id']:q for p in (directory/'rubrics').glob('*.json') for q in json.loads(p.read_text()).get('part',{}).get('questions',[])}
            for q in resource['questions']:
                expected=generated[q['id']]
                for field in (*SOURCE_FIELDS,'criteria','rubric_levels','rubric_version'):
                    if q.get(field)!=expected.get(field):raise ValueError('Stored question changed: '+q['id']+' '+field)
            write(args.fixture/'bank.json',resource)
            write(directory/'persistence.json',{'ok':True,'questions':80,'job_status':stored_job['status'],
                                               'resource_version':resource['resource_version'],'live_calls':0})
    finally:
        config.PEN_DIR,core._llm,core.launch=old_home,old_llm,old_launch


def report(args):
    manifest=frozen(args.fixture);directory=args.fixture/'runs'/args.run
    rubric={p.stem:json.loads(p.read_text()) for p in (directory/'rubrics').glob('*.json')}
    grades=[json.loads(p.read_text()) for p in (directory/'grades').glob('*.json')]
    calls=[json.loads(p.read_text()) for p in (args.fixture/'runs').glob('*/calls/*.json')]
    summary={'campaign_calls':len(calls),'campaign_tokens':sum(r['charged_tokens'] for r in calls),
             'rubrics_expected':80,'rubrics_attempted':len(rubric),'rubrics_valid':sum(r['ok'] for r in rubric.values()),'splits':{}}
    probes=json.loads((args.fixture/'probe-answers.json').read_text())
    for split in ('dev','test','all'):
        group=[g for g in grades if split=='all' or g['split']==split]
        good=[g for g in group if g['ok']]
        expected=sum(split=='all' or manifest['split'][p['case_id']]==split for p in probes)
        score_by_id={g['id']:g['score'] for g in good}
        selected={p['case_id'] for p in probes if split=='all' or manifest['split'][p['case_id']]==split}
        increasing=sum(all(ident+'-'+k in score_by_id for k in ('basic','advanced','full')) and
                       score_by_id[ident+'-basic']<score_by_id[ident+'-advanced']<score_by_id[ident+'-full'] for ident in selected)
        summary['splits'][split]={'expected':expected,'attempted':len(group),'valid':len(good),
            'mae':sum(abs(g['score']-g['expected']) for g in good)/len(good) if good else None,
            'within_10_points_fraction':sum(abs(g['score']-g['expected'])<=.100001 for g in good)/expected,
            'strictly_increasing':increasing,'sampled_questions':len(selected),
            'wrong_credited':[g['id'] for g in good if g['kind']=='wrong' and g['score']>0],
            'failures':[g for g in group if not g['ok'] or abs(g['score']-g['expected'])>.100001]}
    write(directory/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['freeze','check','rubric','grade','persist','report'])
    parser.add_argument('--fixture',type=Path,default=ROOT/'evals/rl_textbook_v1')
    parser.add_argument('--run',default='baseline-v1')
    parser.add_argument('--split',choices=['dev','test','all'],default='all')
    parser.add_argument('--cases',default='')
    parser.add_argument('--reuse-rubrics-from',default='',help='Replay unchanged rubric requests from a prior run without paying twice')
    parser.add_argument('--workers',type=int,choices=range(1,5),default=4)
    parser.add_argument('--key-file',type=Path,default=Path.home()/'dev/Socrates-agent/.env')
    args=parser.parse_args();args.fixture=args.fixture.resolve()
    if args.phase=='freeze':freeze(args.fixture)
    elif args.phase=='check':
        if (args.fixture/'manifest.json').exists():frozen(args.fixture)
        bank=check(args.fixture);print('Validated',len(bank['questions']),'questions')
    elif args.phase in {'rubric','grade'}:run_paid(args)
    elif args.phase=='persist':persist(args)
    else:report(args)


if __name__=='__main__':main()
