import json
from pathlib import Path
import pytest
from pen.config import LLMConfig
from pen.practice.evaluation import Recorder,EvaluationError


def test_recorder_replays_without_paying_and_enforces_campaign_budget(tmp_path,monkeypatch):
    hits=[]
    class Fake:
        def __init__(self,*a,**kw):self.last_request=None;self.last_response=None;self.usage={}
        def __call__(self,system,payload):
            hits.append(1);self.last_request={'messages':[]}
            self.last_response={'usage':{'total_tokens':10},'choices':[]};self.usage={'total_tokens':10}
            return {'answer':7}
    monkeypatch.setattr('pen.practice.evaluation.JsonLLMClient',Fake)
    cfg=LLMConfig('https://api.deepseek.com','test-secret','deepseek-flash','test')
    r=Recorder(tmp_path/'campaign/first',cfg,max_calls=1,max_tokens=10000)
    assert r.call('one','s',{'x':1})=={'answer':7}
    assert r.call('one','s',{'x':1})=={'answer':7}
    assert len(hits)==1
    with pytest.raises(EvaluationError,match='changed'):r.call('one','s',{'x':2})
    other=Recorder(tmp_path/'campaign/second',cfg,max_calls=1,max_tokens=10000)
    with pytest.raises(EvaluationError,match='budget'):other.call('two','s',{})
    assert len(hits)==1


def test_invalid_json_is_recorded_not_retried_and_error_redacts_key(tmp_path,monkeypatch):
    class Fake:
        def __init__(self,*a,**kw):self.last_request={};self.last_response={'usage':{'total_tokens':15},'choices':[]};self.usage={}
        def __call__(self,*args):raise ValueError('bad JSON test-secret')
    monkeypatch.setattr('pen.practice.evaluation.JsonLLMClient',Fake)
    r=Recorder(tmp_path/'campaign/first',LLMConfig('https://api.deepseek.com','test-secret','deepseek-flash','test'))
    with pytest.raises(EvaluationError):r.call('bad','s',{})
    records=list((tmp_path/'campaign/first/calls').glob('*.json'))
    assert len(records)==1 and 'test-secret' not in records[0].read_text()
    row=json.loads(records[0].read_text())
    assert row['charged_tokens']==15 and not row['retryable']
