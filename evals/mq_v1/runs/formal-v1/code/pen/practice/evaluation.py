"""Recorded, bounded real-model evaluation transport (never used by the UI)."""
from __future__ import annotations
import json
from pathlib import Path
import threading
import time
from typing import Any

from pen.config import LLMConfig
from pen.practice.contracts import fingerprint, timestamp
from pen.practice.llm import JsonLLMClient, parse_json_object


class EvaluationError(RuntimeError):
    pass


class Recorder:
    def __init__(self, directory: Path, cfg: LLMConfig, *, max_calls: int=800, max_tokens: int=4_000_000):
        self.directory, self.cfg = directory, cfg
        self.lock = threading.Lock()
        self.max_calls, self.max_tokens = max_calls, max_tokens
        self.inflight_tokens = 0
        (directory/'calls').mkdir(parents=True,exist_ok=True)
        # Development retries and formal runs share one campaign ceiling.
        records = [json.loads(p.read_text()) for p in directory.parent.glob('*/calls/*.json')]
        self.calls = len(records)
        self.tokens = sum(r.get('charged_tokens',0) for r in records)

    def call(self, ident: str, system: str, payload: dict[str,Any]) -> dict[str,Any]:
        signature=fingerprint([self.cfg.model,self.cfg.base_url,system,payload])
        for attempt in range(2):
            path=self.directory/'calls'/f'{ident}--{attempt}.json'
            if path.is_file():
                row=json.loads(path.read_text())
                if row['signature'] != signature:
                    raise EvaluationError('Frozen request changed: '+ident)
                if row.get('ok'):
                    return row['parsed']
                if not row.get('retryable') or attempt==1:
                    raise EvaluationError(row.get('error','recorded failure'))
                continue
            text_payload={k:v for k,v in payload.items() if k!='_images'}
            reserve=len((system+json.dumps(text_payload,ensure_ascii=False)).encode())+2048+1024*len(payload.get('_images',[]))+512
            with self.lock:
                if self.calls>=self.max_calls or self.tokens+self.inflight_tokens+reserve>self.max_tokens:
                    raise EvaluationError('Evaluation budget exhausted; no further request started')
                self.calls+=1;self.inflight_tokens+=reserve
            client=JsonLLMClient(self.cfg,lang='zh',timeout=75,max_output_tokens=2048,temperature=0)
            start=time.monotonic()
            row={'id':ident,'attempt':attempt,'signature':signature,'started_at':timestamp(),
                 'requested_model':self.cfg.model,'endpoint':self.cfg.base_url,'ok':False,'retryable':False}
            try:
                row['parsed']=client(system,payload)
                row['ok']=True
            except Exception as exc:
                import openai
                row['error']=str(exc).replace(self.cfg.api_key,'[REDACTED]')
                row['retryable']=isinstance(exc,(openai.APIConnectionError,openai.APITimeoutError)) or (isinstance(exc,openai.APIStatusError) and (exc.status_code==429 or exc.status_code>=500))
            row.update(elapsed_seconds=round(time.monotonic()-start,3),request=client.last_request,
                       response=client.last_response,usage=client.usage)
            usage=(client.last_response or {}).get('usage') or {}
            actual=usage.get('total_tokens')
            # Lost responses may still be billed: keep a conservative reservation.
            row['charged_tokens']=int(actual) if actual is not None else reserve
            row['usage_known']=actual is not None
            with self.lock:
                self.inflight_tokens-=reserve
                self.tokens+=row['charged_tokens']
                path.write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n')
            if row['ok']:
                return row['parsed']
            if not row['retryable']:
                raise EvaluationError(row['error'])
        raise EvaluationError(row.get('error','request failed'))

    def for_case(self, ident: str):
        return lambda system,payload:self.call(ident,system,payload)
