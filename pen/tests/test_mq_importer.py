from pathlib import Path
import json
import pytest
from pen.practice.importer import parse_handbook,question_images
from pen.practice.llm import parse_json_object

FIX=Path(__file__).resolve().parents[2]/'evals'/'mq_v1'

def explicit_levels(result):
    for c in result['criteria']:
        c['levels']=[{'id':'missing','score':0,'condition':'错误或缺失'}, {'id':'met','score':c['max_score'],'condition':c['description']}]
    return result

def test_frozen_book_imports_exact_source_fields_and_media():
    data=parse_handbook(FIX/'book.md')
    expected=json.loads((FIX/'expected.json').read_text())
    assert len(data['questions'])==30 and len(data['skipped_units'])==2 and not data['issues']
    assert sum(bool(q['assets']) for q in data['questions'])==8
    for q,e in zip(data['questions'],expected):
        for key in ('title','type','prompt','reference_answer','solution_markdown'):
            assert q[key]==e[key]
        if e['choices']:assert q['choices']==e['choices']
        assert 'BODY_SENTINEL' not in json.dumps(q,ensure_ascii=False)
        if q['assets']:assert question_images(q)
    assert len({q['id'] for q in data['questions']})==30
    assert data['source_revision']==parse_handbook(FIX/'book.md')['source_revision']


def test_rich_json_does_not_strip_embedded_fences():
    obj={'prompt':'```python\nprint(1)\n```','solution':'```mermaid\ngraph LR\nA-->B\n```'}
    raw=json.dumps(obj)
    assert parse_json_object(raw)==obj
    assert parse_json_object('```json\n'+raw+'\n```')==obj
    with pytest.raises(ValueError):parse_json_object('Words before '+raw)


def source():
    return '# Unit\n## Theory\nbody\n## meta question\n### Same title\n#### 题型\nsingle_choice\n#### 题干\nWhich?\n#### 选项\n##### A\nOne\n##### B\nTwo\n#### 答案\nB\n#### 解答\nTwo.\n'

@pytest.mark.parametrize('text,code',[(source()+'## Later\nText','mq_not_unique_final_section'),(source()+'## meta question\n','mq_not_unique_final_section'),(source().replace('#### 答案\nB','#### 答案\nC'),'invalid_question'),(source().replace('Which?','![x](missing.png)'),'invalid_question')])
def test_invalid_sections_quarantined(tmp_path,text,code):
    p=tmp_path/'book.md';p.write_text(text)
    data=parse_handbook(p)
    assert not data['questions'] and any(i['code']==code for i in data['issues'])


def test_code_quote_and_duplicate_titles_do_not_split_wrongly(tmp_path):
    text=source().replace('Which?','Which?\n\n~~~~markdown\n# Fake\n## meta question\n### Fake\n~~~~\n\n> # Quoted\n> ## meta question\n')
    text+='\n'+source().split('### Same title',1)[1].join(['### Same title',''])
    p=tmp_path/'book.md';p.write_text(text)
    data=parse_handbook(p)
    assert len(data['units'])==1 and len(data['questions'])==2
    assert len({q['id'] for q in data['questions']})==2
    assert '~~~~markdown' in data['questions'][0]['prompt']


def test_rubric_derivation_preserves_existing_question():
    from pen.practice.importer import derive_rubric
    q=parse_handbook(FIX/'book.md')['questions'][0]
    def fake(system,payload):
        assert 'Do not generate' in system
        return explicit_levels({'points':[{'id':'p','name':'运算顺序','definition':'先乘后加'}], 'criteria':[{'id':'c','point_id':'p','max_score':1,'description':'正确选项A','partial_credit':'选A得1，否则0'}]})
    result=derive_rubric(q,fake)
    assert len(result['questions'])==1
    for field in ['id','version','type','prompt','choices','correct_choice_id','reference_answer','solution_markdown']:
        assert result['questions'][0][field]==q[field]


def test_h3_gateway_build_imports_one_source_question_without_generating_variants(tmp_path,monkeypatch):
    from pen.practice import coordinator as core, resources
    from pen.practice.runtime import set_enabled
    p=tmp_path/'book.md';p.write_text(source())
    set_enabled('scope',True)
    def fake(system,payload):
        return explicit_levels({'points':[{'id':'p','name':'One','definition':'One fact'}], 'criteria':[{'id':'c','point_id':'p','max_score':1,'description':'B','partial_credit':'B gives1, other0'}]})
    monkeypatch.setattr(core,'_llm',lambda *args:fake)
    monkeypatch.setattr(core,'launch',lambda key,fn,*args:fn(*args))
    monkeypatch.setattr(resources,'compile_meta_question',lambda *a,**kw:pytest.fail('Generated variants during import'))
    core.start_build('scope','book',p,object())
    resource=core.active_resource('scope','book')
    assert len(resource['questions'])==1
    assert resource['questions'][0]['prompt']=='Which?'
    assert resource['questions'][0]['correct_choice_id']=='B'


def test_asset_bytes_are_part_of_question_version(tmp_path):
    p=tmp_path/'book.md';p.write_text(source().replace('Which?','![图](plot.png)'))
    image=tmp_path/'plot.png';image.write_bytes((FIX/'assets/v01.png').read_bytes())
    first=parse_handbook(p)['questions'][0]
    image.write_bytes((FIX/'assets/v02.png').read_bytes())
    second=parse_handbook(p)['questions'][0]
    assert first['id']==second['id']
    assert first['version']!=second['version']
    with pytest.raises(ValueError,match='changed'):question_images(first)


def test_supplemental_rich_markdown_and_invalid_unit():
    rich=parse_handbook(FIX/'cases/rich_content.md')
    assert len(rich['questions'])==1 and not rich['issues']
    prompt=rich['questions'][0]['prompt']
    for fragment in ('$x^2+y^2=z^2$','| A | 2 |','##### 题干中的五级标题','###### 题干中的六级标题','```mermaid\ngraph LR\n    A --> B','### 伪题目'):
        assert fragment in prompt
    bad=parse_handbook(FIX/'cases/not_final.md')
    assert not bad['questions'] and bad['issues'][0]['code']=='mq_not_unique_final_section'
    empty=parse_handbook(FIX/'cases/no_questions.md')
    assert not empty['questions'] and len(empty['skipped_units'])==1
