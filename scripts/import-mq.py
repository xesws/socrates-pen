"""Lossless deterministic textbook -> JSON + local assets. No model/API calls."""
from pathlib import Path
import argparse,json,shutil,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pen.practice.importer import parse_handbook

def main():
    p=argparse.ArgumentParser();p.add_argument('textbook',type=Path);p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    result=parse_handbook(args.textbook)
    args.out.mkdir(parents=True,exist_ok=True)
    for q in result['questions']:
        source=Path(q.pop('asset_root'))
        for a in q['assets']:
            dest=args.out/a['relative_path'];dest.parent.mkdir(parents=True,exist_ok=True)
            original=(source/a['relative_path']).resolve()
            if original!=dest.resolve():shutil.copyfile(original,dest)
    # meta_questions point to the same question objects; no machine-specific roots remain.
    (args.out/'bank.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'questions':len(result['questions']),'skipped_units':len(result['skipped_units']),'issues':result['issues'],'output':str(args.out/'bank.json')},ensure_ascii=False))
    return 1 if result['issues'] else 0
if __name__=='__main__':raise SystemExit(main())
