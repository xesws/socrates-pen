"""python -m pen  → 起 API；python -m pen.index --check <md> 见 index.py。"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="苏格拉底")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run("pen.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
