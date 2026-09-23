import base64
import copy
from pathlib import Path

import pytest

from pen.practice.grading import PracticeGradeError, grade_answer, validate_answer
from pen.practice.importer import RUBRIC_SYSTEM, derive_rubric
from pen.practice.rubric import validate_levels
from pen.practice.rubric_examples import EXAMPLES


def question():
    example = copy.deepcopy(EXAMPLES[0])
    return {**example, **example.pop('rubric'), 'id': 'q1', 'source_mq_id': 'mq1', 'assets': []}


def image():
    blob = (Path(__file__).resolve().parents[2] / 'evals/mq_v1/assets/v01.png').read_bytes()
    return {'mime': 'image/png', 'data': base64.b64encode(blob).decode()}


def response(q, met, *, image_answer=False):
    evidence = '; '.join(c['description'] for c in q['criteria'] if c['id'] in met)
    return {'criteria': [
        {'criterion_id': c['id'], 'level_id': 'met' if c['id'] in met else 'missing',
         'reason': 'matches' if c['id'] in met else 'missing',
         'evidence_quote': c['description'] if c['id'] in met else '',
         'evidence_source': 'image' if image_answer else 'text',
         **({'image_index': 0} if image_answer else {})}
        for c in q['criteria']],
        **({'transcriptions': [{'image_index': 0, 'text': evidence}]} if image_answer else {})}


def test_all_ten_demonstrations_have_executable_cumulative_bands_and_are_in_prompt():
    assert len(EXAMPLES) >= 10
    for example in EXAMPLES:
        assert example['id'] in RUBRIC_SYSTEM
        q = {**example, **example['rubric']}
        validate_levels(q['criteria'], q['rubric_levels'])
        for sample in example['calibration']:
            grade = grade_answer(q, {'text': sample['answer']}, lambda *a: response(q, sample['met_criteria']))
            assert grade['score'] * 100 == pytest.approx(sample['score_out_of_100'])
        assert len(q['criteria']) == 8


def test_uploaded_answer_is_direct_image_input_with_no_choice_conversion():
    q = question()
    def llm(system, payload):
        assert payload['question']['type'] == 'short_answer'
        assert payload['answer_text'] == ''
        assert 'images' not in payload['answer']
        assert payload['_images'] == [image()]
        assert payload['image_roles'] == [{'role': 'learner_answer', 'image_index': 0}]
        return response(q, ['c1', 'c2'], image_answer=True)
    grade = grade_answer(q, {'images': [image()]}, llm)
    assert grade['score'] == .2
    assert grade['achievement']['id'] == 'basic'
    assert len(grade['transcriptions']) == 1


def test_image_fill_uses_model_and_choices_reject_images():
    c = copy.deepcopy(question()['criteria'][0]); c.update(max_score=1, levels=[{'id':'missing','score':0,'condition':'incorrect'}, {'id':'met','score':1,'condition':'2'}], blank_id='b1')
    q = {'type':'fill_blank','prompt':'1+1={{b1}}','reference_answer':'2', 'blanks':[{'id':'b1','answers':['2'],'grading':'exact'}], 'criteria':[c]}
    result = {'criteria':[{'criterion_id':'c1','level_id':'met','reason':'correct','evidence_quote':'2','evidence_source':'image','image_index':0}], 'transcriptions':[{'image_index':0,'text':'b1: 2'}]}
    assert grade_answer(q, {'images':[image()]}, lambda *a: result)['score'] == 1
    q['type']='single_choice'
    with pytest.raises(PracticeGradeError, match='Choice answers'):
        validate_answer(q, {'choice_id':'A','images':[image()]})


def test_unreadable_answer_never_becomes_zero_and_fake_image_evidence_is_rejected():
    q = question()
    with pytest.raises(PracticeGradeError) as exc:
        grade_answer(q, {'images':[image()]}, lambda *a: {'status':'insufficient_evidence','reason':'blurred'})
    assert exc.value.code == 'insufficient_evidence'
    raw = response(q, ['c1'], image_answer=True)
    raw['criteria'][0]['image_index'] = 9
    with pytest.raises(PracticeGradeError, match='learner image'):
        grade_answer(q, {'images':[image()]}, lambda *a: raw)


def test_score_mapping_cannot_be_overridden_by_model_and_band_is_not_just_total():
    q = question()
    met = ['c3','c4','c5','c6','c7','c8']
    raw = response(q, met)
    answer = {'text': q['reference_answer']}
    grade = grade_answer(q, answer, lambda *a: raw)
    assert grade['score'] == pytest.approx(.8)
    assert grade['achievement'] is None  # basic facts absent despite high numeric score
    raw['criteria'][0]['score'] = 1
    with pytest.raises(PracticeGradeError, match='contradicts'):
        grade_answer(q, answer, lambda *a: raw)


def test_invalid_band_or_unquantified_rubric_is_rejected():
    q = question()
    q['rubric_levels'][1]['min_score'] = .9
    with pytest.raises(PracticeGradeError, match='threshold'):
        grade_answer(q, {'text':'answer'}, lambda *a: {})
    q = question()
    raw = copy.deepcopy(EXAMPLES[0]['rubric'])
    del raw['criteria'][0]['levels']
    with pytest.raises(ValueError, match='explicit score levels'):
        derive_rubric(q, lambda *a: raw)


@pytest.mark.parametrize('readable', [True, False])
def test_uploaded_answer_persists_but_unreadable_never_emits_learning_evidence(monkeypatch, readable):
    from pen.practice import coordinator as core
    from pen.practice.runtime import set_enabled
    from pen.practice.store import Store
    q = question()
    q.update(version='v1', purpose='practice', point_ids=[c['point_id'] for c in q['criteria']])
    scope, sid, hid = 'image-test', 'session1', 'book1'
    set_enabled(scope, True)
    store = Store()
    store.put(scope, 'session', sid, hid, {
        'id':sid, 'handbook_id':hid, 'mode':'practice','status':'active',
        'questions':[q], 'attempt_ids':[], 'drafts':{}, 'recommendations':[],
        'resource_version':'v1', 'blueprint':{'point_weights':{'p1':1}},
    })
    def llm(system, payload):
        assert payload['_images'] == [image()]
        return response(q, ['c1','c2'], image_answer=True) if readable else {'status':'insufficient_evidence','reason':'cannot read handwriting'}
    monkeypatch.setattr(core, '_llm', lambda *a: llm)
    monkeypatch.setattr(core, 'launch', lambda key, fn, *a: fn(*a))
    result = core.submit(scope, sid, q['id'], {'images':[image()]}, 'submission1', 30, None)
    attempt = result['attempts'][0]
    assert store.get(scope, 'attempt', attempt['id'])['answer']['images'] == [image()]
    events = store.events(scope, hid)
    if readable:
        assert attempt['status']=='graded' and attempt['grade']['score']==.2
        assert sum(e['type']=='attempt_graded' for e in events)==1
    else:
        assert attempt['status']=='failed' and attempt['error_code']=='insufficient_evidence'
        assert 'grade' not in attempt and not events
