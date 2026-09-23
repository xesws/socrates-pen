# Practice protocol 1

Internal ownership: gateway owns practice.sqlite; analysis owns analysis.sqlite;
scheduling owns scheduling.sqlite. All entities carry scope (hash of resolved vault
root) and handbook_id. No worker accesses another owner's files. UTC ISO timestamps.

## Canonical dictionaries

- MQ: `{id, version, title, text, answer, source:{start_line,end_line}, chapter}`.
  text includes ONLY the original MQ block. version is content hash.
- Point: `{id,name,definition,source_mq_ids:[id],evidence:[{mq_id,quote}]}`.
- Edge: `{from,to,type:"requires"|"related"|"contrasts",evidence:[{mq_id,quote}]}`.
- Question: `{id,version,source_mq_id,type:"single_choice"|"fill_blank"|"short_answer",
  purpose:"practice"|"exam",exam_form:0|1|2|null,prompt,point_ids,status:"accepted",
  estimated_seconds,choices?:[{id,text}],correct_choice_id?,
  blanks?:[{id,label,answers:[string],case_sensitive?,numeric_tolerance?,numeric_value?,grading:"exact"|"semantic"}],
  reference_answer,criteria:[{id,point_id,max_score,description,partial_credit,
  blank_id?,evidence:[{mq_id,quote}]}]}`. For `fill_blank`, each criterion maps
  to exactly one blank via `blank_id`; a single blank question may infer it.
- Answer: `{choice_id?:string,blanks?:{[blank_id]:string},text?:string}`.
- Grade: `{criteria:[{criterion_id,point_id,score,max_score,reason,evidence_quote}],
  score,max_score,model,usage?}`. Totals recomputed by gateway.
- Attempt: `{id,session_id,question_id,question_version,source_version,purpose,answer,
  status:"pending"|"graded"|"failed"|"skipped",created_at,answered_at,duration_seconds,
  decision_id?,grade?,error?}`. question metadata may be included as `question` in events.
- Blueprint: `{id,version,point_weights:{[point_id]:number},target_score:80,target_date:
  "YYYY-MM-DD",daily_minutes:20,source_mq_ids:[id],stable_tolerance:10}`.
- Event: `{seq,type:"attempt_graded"|"session_completed",payload,created_at}`.
  seq monotonic database-wide; scoped events can have gaps. Never require contiguous seq.
- Snapshot: `{resource_version,revision,points,edges,questions:[question_metadata],blueprint}`.
  revision is an ISO UTC timestamp; workers reject older snapshot replacements while
  still consuming their unseen events. Blueprint also retains resource_version and updated_at.

## Workers

CLI `python -m pen.practice.worker --service analysis|scheduling --parent-pid PID
--ready-file PATH --pen-home PATH`. Secret via PEN_PRACTICE_TOKEN environment.
Ready file JSON `{port,pid,service,protocol:1}` after listening on loopback port 0.
Bearer authentication on all routes, including health. Parent death stops child.

- GET /v1/health -> `{status:"ok",protocol:1,service,pid}`.
- POST /v1/sync `{scope,handbook_id,snapshot,events}` -> `{cursor}`.
- POST /v1/analysis `{scope,handbook_id,now?}` -> `{points:[{id,name,n,score,
  expected_score,status,evidence,last_seen,due_at}],directions,model,resource_version,cursor}`.
  Scores normalized 0..1, score/expected_score nullable. model is an object with status/version.
- POST /v1/recommendations `{scope,handbook_id,analysis,now?,minutes?}` ->
  `{items:[{question_id,decision_id,point_id,reason,estimated_seconds,due_at,probability}],
  model,cursor}`. Candidate metadata and blueprint from sync. Each emitted decision
  durable, with context/candidates/propensity; unanswered is not a graded outcome.

## Frontend gateway API

All routes prefix /v1/practice. All requests include vault_root (query for GET,
body for mutations). Gateway resolves scope. LLM fields same as llmPayload;
never send API key from frontend.

- PUT /enable `{vault_root,enabled}` -> `{enabled,services}`.
- GET /state?vault_root=&handbook_id= -> `{enabled,resource|null,job|null,blueprint|null,
  sessions:[],services,stats}`. resource includes MQs/points/edges/issues, no answers or exam prompts.
- POST /build `{vault_root,handbook_id,practice_per_type?:2,exam_forms?:3,regenerate?:false,...llm}` -> job.
  regenerate explicitly creates fresh variants; normal builds reuse unchanged accepted bundles.
- GET /jobs/{id}?vault_root= -> job `{id,status,completed,total,issues,usage,...}`.
  status: queued, running, completed, completed_with_issues, failed, cancelled,
  stale, interrupted. Invalid or missing-answer MQs are reported before generation.
- POST /jobs/{id}/cancel or /resume `{vault_root,...llm}` -> job.
- PUT /blueprint `{vault_root,handbook_id,blueprint}` -> blueprint.
- GET /questions?vault_root=&handbook_id= -> `{questions:[public practice questions]}`.
- POST /sessions `{vault_root,handbook_id,mode:"practice"|"recommended"|"exam",
  question_ids?:[],minutes?:20,...llm}` -> session.
- GET /sessions/{id}?vault_root= -> session.
- PUT /sessions/{id}/draft `{vault_root,question_id,answer}` -> `{ok:true}`.
- POST /sessions/{id}/answers `{vault_root,question_id,answer,idempotency_key,
  duration_seconds,skip?:false,...llm}` -> session.
- POST /sessions/{id}/finish `{vault_root,...llm}` -> session; exam grading starts here.
- POST /attempts/{id}/retry `{vault_root,...llm}` -> attempt (only failed grades).
- GET /analysis?vault_root=&handbook_id= -> analysis worker report plus `exams` summary.
- POST /recommendations `{vault_root,handbook_id,minutes?}` -> scheduling result.

Session: `{id,handbook_id,mode,status:"active"|"grading"|"completed",questions:[public],
 attempts:[public_attempt],drafts:{[question_id]:answer},recommendations:[],created_at,
 blueprint,exam_form?,result?}`. Gateway redacts all grade/feedback until exam finish.
After grading, public attempt includes `grade`, `reference_answer`, `criteria`.
Session questions frozen; current source edits don't alter a started session.
