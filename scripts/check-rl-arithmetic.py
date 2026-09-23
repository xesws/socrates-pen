"""Primary reviewer's arithmetic recomputations, independent of authored answers."""
from pathlib import Path
import json
import math

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'evals/rl_textbook_v1'

CALCULATIONS={
    'RL01Q05':('sum(2 * 0.5**t for t in range(3))',sum(2*.5**t for t in range(3))),
    'RL02Q05':('1 + 0.9 * 5',1+.9*5),
    'RL02Q06':('0.25 * 2 + 0.75 * 6',.25*2+.75*6),
    'RL03Q05':('max(0.2 + 0.9 * 4, 1 + 0.9 * 2)',max(.2+.9*4,1+.9*2)),
    'RL04Q05':('(2 + 4 + 6) / 3',(2+4+6)/3),
    'RL04Q06':('count after the tenth sample',10),
    'RL04Q07':('1 - 0.2 + 0.2 / 4',1-.2+.2/4),
    'RL05Q05':('1 + 0.9 * 4 - 3',1+.9*4-3),
    'RL05Q06':('0 + 0.9 * 5',.9*5),
    'RL05Q07':('-1 + 0.5 * max(2,6,4)',-1+.5*max(2,6,4)),
    'RL06Q05':('1 * 3 + 2 * 4',1*3+2*4),
    'RL06Q06':('1 + 0.99 * 8',1+.99*8),
    'RL07Q05':('exp(log(3)) / (exp(0) + exp(log(3)))',math.exp(math.log(3))/(math.exp(0)+math.exp(math.log(3)))),
    'RL07Q06':('5 - 3',5-3),
    'RL08Q05':('1 - 0.1 + 0.1 / 5',1-.1+.1/5),
    'RL08Q06':('0.4 / 0.2',.4/.2),
    'RL08Q07':('10 - 1.96 * 2',10-1.96*2),
}


def main():
    raw=json.loads((FIX/'expected.json').read_text())
    questions={q['title']:q for q in raw['questions']}
    rows=[]
    for ident,(expression,result) in CALCULATIONS.items():
        blank=questions[ident]['blanks'][0]
        expected=blank['numeric_value'];tolerance=blank['numeric_tolerance']
        matches=abs(result-expected)<=max(tolerance,1e-12)
        rows.append({'id':ident,'expression':expression,'recomputed':result,'authored':expected,'ok':matches})
    path=FIX/'arithmetic-check.json'
    path.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
    if not all(row['ok'] for row in rows):raise SystemExit('Arithmetic mismatch: '+str([r for r in rows if not r['ok']]))
    print('Independent arithmetic checks:',len(rows),'passed')


if __name__=='__main__':main()
