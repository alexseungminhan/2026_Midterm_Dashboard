"""python -m election2026 — CLI entry point.

    run             full pipeline -> data/forecast.json (+ dated snapshot)
      --chamber       senate | house | governor
      --races GA,MI   state codes and/or race_ids
      --skip fec,...  skip specific Track B sources (quota conservation)
      --rank-by       volume1wk (default) | volume — see board.py
    make-templates  blank manual-input spreadsheets into data/manual/
    validate        check an existing forecast.json against the schema
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="election2026",
                                 description="2026 미국 중간선거 모니터")
    sub = ap.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run the full pipeline")
    run_p.add_argument("--chamber", choices=["senate", "house", "governor"])
    run_p.add_argument("--races", help="comma-separated state codes / race_ids")
    run_p.add_argument("--skip", help="comma-separated Track B sources to skip")
    run_p.add_argument("--output", help="alternative output path")
    run_p.add_argument("--rank-by", choices=["volume1wk", "volume"],
                       help="which trading volume orders the board "
                            "(default: config.BOARD['rank_by'])")

    imp = sub.add_parser("import-polls",
                         help="convert NYT poll workbooks into data/manual/polls")
    imp.add_argument("source", nargs="+",
                     help="paths to the transcribed .xlsx workbooks (Senate "
                          "and/or House). The output is the UNION, so pass "
                          "every workbook together — importing one alone "
                          "drops the others' polls.")
    imp.add_argument("--include-independents", action="store_true",
                     help="also import races led by an independent (their "
                          "margin is not a D-vs-R probability — see "
                          "import_polls.py)")

    val = sub.add_parser("validate", help="validate a forecast.json")
    val.add_argument("path", nargs="?", default=None)

    vlog = sub.add_parser("verify-log",
                          help="verify the sealed prediction log chain")
    vlog.add_argument("path", nargs="?", default=None)

    args = ap.parse_args(argv)

    if args.command == "run":
        from . import pipeline
        kwargs = dict(chamber=args.chamber, rank_by=args.rank_by)
        if args.races:
            kwargs["races_filter"] = args.races.split(",")
        if args.skip:
            kwargs["skip_sources"] = set(args.skip.split(","))
        if args.output:
            kwargs["output_path"] = args.output
        pipeline.run(**kwargs)


    elif args.command == "import-polls":
        from . import import_polls
        try:
            import_polls.run(args.source,
                             include_independents=args.include_independents)
        except import_polls.PollImportError as exc:
            print("[import-polls] %s" % exc)
            sys.exit(1)

    elif args.command == "validate":
        from . import pipeline, schema
        path = args.path or pipeline.OUTPUT_PATH
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        try:
            schema.validate(doc)
        except schema.SchemaError as exc:
            print(exc)
            sys.exit(1)
        print("[validate] %s conforms to schema v%s"
              % (path, schema.SCHEMA_VERSION))

    elif args.command == "verify-log":
        from . import prediction_log
        path = args.path or prediction_log.LOG_PATH
        ok, problems = prediction_log.verify(path)
        for p in problems:
            print("[verify-log] %s" % p)
        if not ok:
            sys.exit(1)
        print("[verify-log] %s: chain intact, timestamps monotonic" % path)
        stats = prediction_log.summarize(path)
        print("[verify-log] %d real, %d dry-run, %d unlabelled (written "
              "before runs were labelled)"
              % (stats["real"], stats["dry_run"], stats["unlabelled"]))


if __name__ == "__main__":
    main()
