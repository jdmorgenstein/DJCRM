"""Command line interface.

    photodedup doctor --icloud ~/export/icloud --google ~/export/takeout
    photodedup index  --library icloud --root ~/export/icloud
    photodedup index  --library google --root ~/export/takeout --takeout
    photodedup match
    photodedup report --out ~/export/report
    photodedup stage  --out ~/export/staging
    photodedup plan   --staging ~/export/staging
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from . import __version__, db, matching, metadata, preflight, report, scan

DEFAULT_DB = "photodedup.sqlite"


def _connect(args: argparse.Namespace):
    return db.connect(args.db)


def cmd_index(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    connection = _connect(args)
    use_exiftool = not args.no_exiftool
    if use_exiftool and not metadata.exiftool_available():
        print("warning: exiftool not found; HEIC and video metadata will be limited", file=sys.stderr)

    def progress(done: int, total: int) -> None:
        if total:
            print(f"\r  {done}/{total} files", end="", flush=True)

    only_paths = None
    if args.upgrade:
        only_paths = scan.paths_needing_upgrade(connection, args.library, thorough=args.thorough)
        if not only_paths:
            print(f"{args.library}: nothing left to upgrade")
            return 0
        print(f"{args.library}: fully fingerprinting {len(only_paths)} unresolved files")

    stats = scan.scan_library(
        connection,
        root,
        args.library,
        workers=args.workers,
        use_exiftool=use_exiftool,
        resume=not args.rescan,
        read_takeout=args.takeout,
        quick=args.quick and not args.upgrade,
        only_paths=only_paths,
        progress=None if args.quiet else progress,
    )
    if not args.quiet:
        print()
    print(
        f"{args.library}: {stats['total']} media files, {stats['scanned']} indexed, "
        f"{stats['skipped']} unchanged, {stats['errors']} errors"
    )
    if args.quick and not args.upgrade:
        print("  quick pass: no images decoded. Run `match`, then re-run with "
              "--upgrade to decode only what it could not resolve.")
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    connection = _connect(args)
    totals = db.counts(connection)
    if not totals.get("icloud") or not totals.get("google"):
        print("error: index both libraries first (see `photodedup index`)", file=sys.stderr)
        return 2

    options = matching.Options(
        phash_threshold=args.phash_threshold,
        dhash_threshold=args.dhash_threshold,
        time_tolerance=args.time_tolerance,
        allow_perceptual_only=args.allow_perceptual_only,
    )
    tally = matching.run(connection, options)

    print(f"iCloud indexed : {tally['icloud_total']}")
    print(f"Google indexed : {tally['google_total']}")
    print()
    for label, key in (
        ("already in iCloud (certain) ", matching.CERTAIN),
        ("already in iCloud (high)    ", matching.HIGH),
        ("needs review                ", matching.REVIEW),
        ("unique to Google            ", matching.UNIQUE),
    ):
        print(f"  {label}: {tally.get(key, 0)}")
    if tally.get("live_pairs_kept"):
        print(f"  Live Photo halves kept back : {tally['live_pairs_kept']}")
    print("\nby tier:")
    for key, count in sorted(tally.items(), key=lambda item: -item[1] if isinstance(item[1], int) else 0):
        if key.startswith("tier:"):
            print(f"  {key[5:]:<22} {count}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    connection = _connect(args)
    outdir = Path(args.out).expanduser()
    summary = report.write_reports(connection, outdir)

    print(f"reports written to {outdir}")
    print(f"  unique assets  : {summary['unique_assets']} "
          f"(from {summary['unique_files']} Takeout files)")
    print(f"  albums to build: {len(summary['unique_albums'])}")
    for confidence, count in sorted(summary["confidence"].items()):
        print(f"  {confidence:<8}: {count}")
    return 0


def cmd_stage(args: argparse.Namespace) -> int:
    connection = _connect(args)
    outdir = Path(args.out).expanduser()
    stats = report.stage(
        connection,
        outdir,
        include_review=args.include_review,
        skip_edited=args.skip_edited,
        copy=args.copy,
        rebuild_albums=args.rebuild_albums,
    )
    print(f"staged {stats['assets']} assets as {stats['links']} entries in {outdir}")
    if stats["existing_album_members"]:
        print(f"  plus {stats['existing_album_members']} iCloud originals, "
              f"so --dup-albums can rebuild the albums around them")
    if stats["skipped_edited"]:
        print(f"  skipped {stats['skipped_edited']} '-edited' derivatives")
    if stats["failed"]:
        print(f"  {stats['failed']} files could not be staged", file=sys.stderr)
    print("\nNext:")
    flag = " --rebuild-albums" if args.rebuild_albums else ""
    print(f"  photodedup plan --staging {shlex.quote(str(outdir))}{flag}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    staging = Path(args.staging).expanduser().resolve()
    quoted = shlex.quote(str(staging))
    options = "--skip-dups --auto-live --exiftool"
    if args.rebuild_albums:
        options += " --dup-albums"

    def command(extra: str) -> str:
        return (
            f"osxphotos import {quoted} --walk \\\n"
            f"    --album '{{filepath.parent.name}}' --relative-to {quoted} \\\n"
            f"    {options} {extra}"
        )

    print("# Dry run first -- nothing is imported, but every album name is printed.")
    print(command("--dry-run --report import-dryrun.csv"))
    print()
    print("# Then the real import.")
    print(command("--resume --report import.csv"))
    print()
    print("# --skip-dups   second safety net: osxphotos checks Photos' own fingerprints")
    print("# --auto-live   re-pairs Takeout's split Live Photos (photo + video, same stem)")
    print("# --exiftool    reads capture date/GPS from the file rather than trusting the filesystem")
    print("# --resume      safe to re-run after an interruption")
    if args.rebuild_albums:
        print("# --dup-albums  adds the photo already in Photos to the album, instead of")
        print("#               importing a second copy of it")
    return 0


_LEVEL_MARK = {preflight.OK: "ok  ", preflight.WARN: "warn", preflight.FAIL: "FAIL"}


def cmd_doctor(args: argparse.Namespace) -> int:
    def expand(value: str | None) -> Path | None:
        return Path(value).expanduser() if value else None

    checks = preflight.run(
        expand(args.icloud), expand(args.google), expand(args.staging),
        args.workers or scan.default_workers(),
    )
    for check in checks:
        print(f"[{_LEVEL_MARK[check.level]}] {check.title:<18} {check.detail}")

    failures = sum(check.level == preflight.FAIL for check in checks)
    warnings = sum(check.level == preflight.WARN for check in checks)
    print()
    print(f"{failures} blocking, {warnings} worth a look")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="photodedup",
        description="Merge a Google Photos Takeout into iCloud without duplicates.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"index database (default: {DEFAULT_DB})")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index = subparsers.add_parser("index", help="fingerprint a library into the index")
    index.add_argument("--library", required=True, choices=["icloud", "google"])
    index.add_argument("--root", required=True, help="directory to scan")
    index.add_argument("--takeout", action="store_true",
                       help="read Google Takeout JSON sidecars and album folders")
    index.add_argument("--workers", type=int, default=0, help="worker processes (default: CPU count)")
    index.add_argument("--no-exiftool", action="store_true", help="use Pillow only")
    index.add_argument("--rescan", action="store_true", help="re-fingerprint unchanged files")
    index.add_argument("--quick", action="store_true",
                       help="skip image decoding: file hash and capture metadata only. "
                            "Enough for the Apple ContentIdentifier and file-hash tiers, "
                            "which resolve most of an Original-quality library, and about "
                            "10x faster. Follow with `match`, then --upgrade.")
    index.add_argument("--upgrade", action="store_true",
                       help="fully fingerprint only the files a --quick pass plus `match` "
                            "left unresolved")
    index.add_argument("--thorough", action="store_true",
                       help="with --upgrade, decode every remaining image rather than only "
                            "the ones the surviving tiers can reach")
    index.add_argument("--quiet", action="store_true")
    index.set_defaults(func=cmd_index)

    match = subparsers.add_parser("match", help="decide which Google files iCloud already has")
    match.add_argument("--phash-threshold", type=int, default=matching.DEFAULT_PHASH_THRESHOLD)
    match.add_argument("--dhash-threshold", type=int, default=matching.DEFAULT_DHASH_THRESHOLD)
    match.add_argument("--time-tolerance", type=int, default=0,
                       help="seconds of slack when comparing UTC capture times")
    match.add_argument("--allow-perceptual-only", action="store_true",
                       help="trust an unambiguous perceptual match with no capture time (riskier)")
    match.set_defaults(func=cmd_match)

    report_parser = subparsers.add_parser("report", help="write CSV reports")
    report_parser.add_argument("--out", required=True, help="output directory")
    report_parser.set_defaults(func=cmd_report)

    stage_parser = subparsers.add_parser("stage", help="build an importable tree of unique assets")
    stage_parser.add_argument("--out", required=True, help="staging directory")
    stage_parser.add_argument("--include-review", action="store_true",
                              help="also stage the review queue (safer: keeps possible duplicates)")
    stage_parser.add_argument("--skip-edited", action="store_true",
                              help="drop Google's '-edited' derivatives")
    stage_parser.add_argument("--copy", action="store_true", help="copy instead of hard-linking")
    stage_parser.add_argument("--rebuild-albums", action="store_true",
                              help="also stage the iCloud originals of album members iCloud "
                                   "already has, so --dup-albums rebuilds the whole album")
    stage_parser.set_defaults(func=cmd_stage)

    plan_parser = subparsers.add_parser("plan", help="print the osxphotos import commands")
    plan_parser.add_argument("--staging", required=True)
    plan_parser.add_argument("--rebuild-albums", action="store_true",
                             help="match `stage --rebuild-albums`; adds --dup-albums")
    plan_parser.set_defaults(func=cmd_plan)

    doctor = subparsers.add_parser("doctor", help="check tooling, sizes and Takeout completeness")
    doctor.add_argument("--icloud", help="iCloud export directory")
    doctor.add_argument("--google", help="Takeout 'Google Photos' directory")
    doctor.add_argument("--staging", help="where the staging tree will be written")
    doctor.add_argument("--workers", type=int, default=0,
                        help="workers to assume for the time estimate (default: CPU count)")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
