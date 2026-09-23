"""Evaluation accounting must not hide missing/failed student grades."""
import argparse
import importlib.util
import json
from pathlib import Path

import pytest


def runner():
    path = Path(__file__).resolve().parents[2] / 'scripts/evaluate-textbook.py'
    spec = importlib.util.spec_from_file_location('textbook_evaluator', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summary_uses_expected_denominator_and_counts_wrong_credit(tmp_path, capsys):
    mod = runner()
    fixture = tmp_path / 'fixture'
    split = {'RL01Q08':'dev','RL02Q08':'test'}
    mod.write(fixture/'manifest.json', {'files':{},'split':split})
    probes = [{'case_id':qid,'kind':kind,'score':score} for qid in split for kind,score in [('basic',.2),('advanced',.5),('full',1),('wrong',0)]]
    mod.write(fixture/'probe-answers.json',probes)
    directory=fixture/'runs/baseline-v1'
    for p in probes:
        if p['case_id']=='RL02Q08' and p['kind']=='full':continue  # never attempted
        row={'id':p['case_id']+'-'+p['kind'],'case_id':p['case_id'],'kind':p['kind'],
             'split':split[p['case_id']],'ok':True,'score':p['score'],'expected':p['score']}
        if p['case_id']=='RL02Q08' and p['kind']=='advanced':
            row.update(ok=False,error='bad JSON');del row['score']
        if p['case_id']=='RL02Q08' and p['kind']=='wrong':row['score']=.05
        mod.write(directory/'grades'/f'{row["id"]}.json',row)
    mod.report(argparse.Namespace(fixture=fixture,run='baseline-v1'))
    summary=json.loads((directory/'summary.json').read_text())
    test=summary['splits']['test']
    assert test['expected']==4 and test['attempted']==3 and test['valid']==2
    assert test['within_10_points_fraction']==.5
    assert test['wrong_credited']==['RL02Q08-wrong']
    assert test['strictly_increasing']==0
    assert summary['splits']['dev']['strictly_increasing']==1
    assert summary['rubrics_expected']==80 and summary['rubrics_valid']==0


def test_frozen_source_change_is_rejected_before_requests(tmp_path):
    import hashlib
    mod=runner()
    (tmp_path/'book.md').write_text('original')
    mod.write(tmp_path/'manifest.json',{'files':{'book.md':hashlib.sha256(b'original').hexdigest()}})
    mod.frozen(tmp_path)
    (tmp_path/'book.md').write_text('changed')
    with pytest.raises(ValueError,match='Frozen source changed'):
        mod.frozen(tmp_path)
