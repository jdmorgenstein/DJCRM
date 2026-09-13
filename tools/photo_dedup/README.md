# Merging Google Photos into iCloud without duplicates

Goal: one iCloud library holding every unique photo from iCloud **and** Google
Photos, with the photo-shoot albums that only ever existed in Google preserved,
and no duplicates — so Google Photos can be cancelled.

The obstacle you identified is the right one: **file hashes are useless here.**
This document explains why, what to use instead, and how to run it.

---

## 1. Why identical photos hash differently

Four separate causes, and they stack:

| Cause | What changes | Pixels identical? |
|---|---|---|
| Takeout rewrites metadata — capture time, GPS and description are moved into a JSON sidecar and often stripped from the file | bytes | **yes** |
| "Storage saver" backup quality — anything over 16 MP is resized to 16 MP, everything is re-compressed as JPEG | bytes + pixels | no |
| HEIC → JPEG — the iPhone shoots HEIC; several upload paths convert to JPEG | bytes + pixels | no |
| Edits — a crop or filter applied on one side only | bytes + pixels | no |

Row 1 is the common case and the most annoying: the picture is *bit-for-bit the
same photograph*, and SHA-256 still disagrees.

So identity has to be established from the **capture**, not from the bytes.

## 2. The signals that actually work

Ordered strongest first. This is the cascade the tool implements.

### Tier 1 — Apple's `ContentIdentifier` (the best signal, and the one people miss)

Every photo an iPhone takes carries a per-capture UUID in the Apple maker note
(EXIF `MakerNotes` tag `0x0011`, `ContentIdentifier`). Two files with the same
value are the same capture — whatever the codec, resolution or metadata. Google
preserves the maker note whenever it hands back your stored original, so this
matches iCloud↔Google directly, with **zero** false-positive risk.

The same table also gives `BurstUUID` (tag `0x000B`), which identifies every
frame of one burst — used below as a safety guard.

### Tier 2 — file SHA-256

Only fires when nothing at all was touched. Kept because it is free and exact.

### Tier 3 — decoded-pixel hash

SHA-256 of the **decoded, orientation-normalised RGB buffer** rather than the
file. This is the direct answer to "the hashes differ": decode both files and
hash the pixels, and a metadata-only rewrite collapses to an exact match. Still
exact — no thresholds, no false positives.

### Tier 4 — capture instant + perceptual hash

For genuine re-encodes (Storage saver, HEIC→JPEG) the pixels *do* differ, so
this tier combines two independent signals:

* **EXIF `DateTimeOriginal` + `SubSecTimeOriginal`.** iPhones write sub-second
  precision, which makes the capture instant effectively unique, and it
  survives re-encoding because Google copies EXIF through.
* **64-bit perceptual hashes** (pHash via DCT, plus dHash), required to be
  within a small Hamming distance.

Plus rejections: different camera model → not a match. Different `BurstUUID` →
not a match. Different sub-second → not a match.

### Tier 5 — perceptual hash alone → **review queue, not deletion**

For files with no usable EXIF (screenshots, WhatsApp images, web uploads).
A tight threshold on *both* hashes, and it still only lands in `review.csv` for
you to eyeball, because this is the one tier that can be wrong.

### Why not perceptual hashing alone?

Because **burst frames are perceptually near-identical.** Measured on this
repository's own fixtures: a frame shifted a few pixels — what the next shot in
a burst looks like — lands at pHash distance 0–6 (median 2), while two genuinely
different photos land at 22–44. A re-encode of a single photo lands in that same
0–6 band. So perceptual distance alone *cannot* separate "re-encoded copy" from
"next frame of the burst", at any threshold. A pHash-only dedupe silently
deletes burst frames, panorama sequences and near-duplicate portraits, and that
is unrecoverable once Google Photos is gone.

What separates them is the sub-second capture time and `BurstUUID`, which is why
tier 4 requires both and tier 5 never deletes.

The whole design follows from one asymmetry: **missing a duplicate costs
storage; a false duplicate costs a photograph.** Every threshold is tuned that
way, and anything uncertain goes to review rather than to the bin.

---

## 3. Getting the two libraries onto disk

### Google Photos → Takeout (the only option)

The Google Photos Library API stopped being usable for this on 31 March 2025:
the `photoslibrary.readonly` scope was removed and now returns
`403 PERMISSION_DENIED`, so third-party tools can no longer read your library.
[Google Takeout](https://takeout.google.com) is the supported route.

* Select **Google Photos** only, `.zip`, 50 GB parts.
* Download and extract **every** part before doing anything else — albums and
  their sidecars can straddle archives.
* Takeout physically copies a photo into each album folder *and* into
  `Photos from YYYY`, so the export contains many internal duplicates. The tool
  collapses those automatically.
* Sidecar naming is a mess — `.json`, `.supplemental-metadata.json`, and
  truncated forms like `.supplemental-metad.json` or `.s.json`, plus counters
  such as `IMG_1.jpg.supplemental-metadata(1).json`. `takeout.py` handles all of
  them, falling back to the `title` field inside each sidecar, which names the
  media file authoritatively.

### iCloud → disk

**With a Mac (strongly preferred).** In Photos, turn on
*Settings → iCloud → Download Originals to this Mac* and **wait for the download
to finish** before exporting. Then use
[osxphotos](https://github.com/RhetTbull/osxphotos):

```bash
osxphotos export ~/export/icloud --update --report icloud-export.csv
```

Live Photo videos, RAW components of RAW+JPEG pairs and edited versions are all
exported by default (there are only `--skip-live` / `--skip-raw` /
`--skip-edited` flags to turn them off). `--update` makes re-runs incremental.

Download the originals locally first rather than reaching for
`--download-missing`: osxphotos' own documentation notes that
`--download-missing` exports only the primary image of a burst and skips the
rest, which would leave burst frames out of the iCloud index.

This export is the important one, because the files are byte-identical to the
originals Photos stores — which is what lets the album-rebuild step below work.

**Without a Mac.** [`icloudpd`](https://github.com/icloud-photos-downloader/icloud_photos_downloader)
downloads originals (HEIC, RAW, 4K, Live Photo `.MOV` pairs) from Windows or
Linux. You will need a Mac or an iPhone for the import step at the end either
way; iCloud for Windows can upload, but it does not offer per-album control.

---

## 4. Running the tool

```bash
cd tools/photo_dedup
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
brew install exiftool          # optional but strongly recommended
```

`exiftool` is not required, but without it HEIC and video metadata is limited —
and HEIC is what your iPhone shoots. Install it.

```bash
export PYTHONPATH=$PWD

# 1. Fingerprint both libraries (slow, resumable, re-runnable).
python -m photodedup index --library icloud --root ~/export/icloud
python -m photodedup index --library google --root "~/export/Takeout/Google Photos" --takeout

# 2. Decide what iCloud already has.
python -m photodedup match

# 3. Read the verdicts before changing anything.
python -m photodedup report --out ~/export/report

# 4. Build an importable tree of what is genuinely missing.
python -m photodedup stage --out ~/export/staging --rebuild-albums

# 5. Print the import commands.
python -m photodedup plan --staging ~/export/staging --rebuild-albums
```

### What step 3 gives you

| File | Contents |
|---|---|
| `duplicates.csv` | every Google file iCloud already has, with the matching iCloud file, the tier that decided it, and the perceptual distance |
| `unique.csv` | one row per genuinely missing picture, with its album(s) |
| `review.csv` | the uncertain ones — **look at these** |
| `albums.csv` | albums to be created, with counts |
| `errors.csv` | files that could not be read |

Spot-check `duplicates.csv` by opening a few pairs side by side. Spot-check
`review.csv` in full — it is deliberately small.

### What step 4 does

Hard-links (no extra disk) the unique assets into one folder per Google album,
collapsing Takeout's internal copies and unioning album membership. Live Photo
halves keep a shared base name so they can be re-paired on import, and if either
half of a pair is unique, both are kept.

`--rebuild-albums` additionally stages the **iCloud original** of every album
member iCloud already has. Those files are byte-identical to what Photos stores,
so `osxphotos import --skip-dups --dup-albums` recognises them by Photos' own
fingerprint, imports nothing, and adds the *existing* photo to the album. That
reproduces a Google album in full — old and new photos together — without
creating a single duplicate asset.

### Step 5: the import

```bash
osxphotos import ~/export/staging --walk \
    --album '{filepath.parent.name}' --relative-to ~/export/staging \
    --skip-dups --auto-live --exiftool --dup-albums --dry-run --report dryrun.csv
```

Run the dry run first and read the report. Then drop `--dry-run` and add
`--resume`.

* `--skip-dups` — a second, independent safety net using Photos' own fingerprints.
* `--auto-live` — Takeout splits a Live Photo into `IMG_1.HEIC` + `IMG_1.MP4`;
  this writes a shared content identifier so Photos re-pairs them.
* `--dup-albums` — makes the album rebuild above work.
* `--exiftool` — take the capture date and GPS from the file, not the filesystem.

Photos then uploads everything to iCloud. **Wait for that upload to finish and
verify on a second device before cancelling Google One.**

---

## 5. Things worth knowing before you start

* **Apple's own Duplicates album will not do this job.** Photos' duplicate
  detection compares identical files; it does not match the same image across
  formats, so a HEIC and its Google JPEG are two photos to it. Useful as a final
  tidy-up, not as the strategy.
* **Check your Google backup quality first** (Google Photos → Settings → Backup
  quality). If it says Storage saver, the Google copies of shared photos are
  degraded and iCloud already has the better version — which is what the tool
  keeps. If it says Original quality, tiers 1–3 will match almost everything and
  the run will be fast and exact.
* **`-edited` files.** Takeout exports both the original and your Google edit.
  Both are kept by default (the edit is a real photo you made); pass
  `--skip-edited` to drop them.
* **Videos** have no perceptual hash here — they are matched on file hash,
  capture time and duration. Anything ambiguous goes to review.
* **Nothing is ever deleted.** The tool only ever hard-links files into a
  staging tree. Keep the Takeout archive until you have verified the import.

## 6. Layout

```
photodedup/
  hashing.py     file / pixel / perceptual hashes, banded index for fast lookup
  appleexif.py   Apple maker-note parser (ContentIdentifier, BurstUUID)
  metadata.py    EXIF extraction via exiftool, with a Pillow fallback
  takeout.py     Takeout sidecar resolution and album detection
  db.py          SQLite index
  scan.py        parallel library walk
  matching.py    the cascade, and the guards that keep bursts intact
  report.py      CSV reports, grouping, staging
  cli.py         command line
tests/           28 tests, including a full synthetic migration
```

Run the tests with:

```bash
python -m unittest discover -s tests -t .
```

### Performance

Measured on 12 MP (4032×3024) JPEGs: **~4.8 files/second/core**, about 210 ms
each, dominated by full-resolution decoding and the pixel hash. On 8 cores that
is roughly 45 minutes for 100k photos — once, since `index` is resumable and
skips unchanged files on re-runs. HEIC decoding is slower than JPEG.

Matching itself takes seconds. The perceptual lookup uses a banded index whose
candidate set is provably exact for the configured threshold (pigeonhole: split
a 64-bit hash into *t+1* bands, and any pair within Hamming distance *t* must
share a whole band), so it never degrades into an all-pairs comparison.

Memory during `match` is one row per iCloud photo held in RAM — on the order of
a few hundred MB for 200k photos.
