# reelsort

A Mac command-line tool that sorts your iCloud Photos library into Instagram Reel
categories (Kids & Family, Fitness, Festivals, Dining, Travel, Lifestyle) using
Apple's PhotoKit and Claude Haiku 5.5.

It never moves, edits, or deletes photos. Later phases only *add* items to albums
inside a `Reels` folder.

## Status

| Phase | What | State |
|---|---|---|
| 1 | Connect to Photos, summarize the library, test image fetching | ✅ this version |
| 2 | Classify a 200-item pilot with Haiku 5.5 (dry run, no changes) | next |
| 3 | Write category albums to Photos | |
| 4 | Full-library run with resume and spend cap | |
| 5 | Nightly automation + "Best of" albums | |

## Requirements

- A Mac on macOS 13 Ventura or later, with **iCloud Photos** turned on in the Photos app
  (Photos > Settings > iCloud).
- Apple's Swift tools. Either install **Xcode** from the App Store, or just the
  smaller Command Line Tools:
  ```bash
  xcode-select --install
  ```
  Check it worked: `swift --version`

## Run Phase 1

```bash
cd reelsort
swift build -c release
.build/release/reelsort scan --thumbnails 10
```

1. macOS will ask to let **Terminal** access your Photos. Choose **Allow Full Access**.
2. You'll get a library summary: photo/video counts, screenshots, vertical videos,
   items per year, and a rough one-time sorting cost estimate.
3. `--thumbnails 10` saves your 10 most recent items as small JPEGs to
   `reelsort-output/`. Open that folder (`open reelsort-output`) and check they
   look right — that proves it can read your photos, including ones stored only
   in iCloud.

### What to send back

Paste the full terminal output (it contains only counts and dates, no images).
Phase 2 is built on those numbers.

## Troubleshooting

- **"Photos access was denied"** — System Settings > Privacy & Security > Photos,
  turn on **Terminal**, set it to Full Access, run again.
- **No permission prompt ever appears / it hangs at "Requesting Photos access"** —
  use the app wrapper instead, which asks for permission as "reelsort":
  ```bash
  ./scripts/make-app.sh
  open -W --stdout "$(tty)" --stderr "$(tty)" build/ReelSort.app --args scan --thumbnails 10
  ```
- **"Access is LIMITED"** — you picked "Limit Access" in the prompt. Change it to
  Full Access in the same Settings screen.
- **Test images fail with "could not load"** — the Mac may be offline or still
  syncing iCloud Photos. Open the Photos app, wait for syncing to finish, retry.
- **Build errors** — copy the full error output back; this code was written in a
  Linux environment without Apple's SDK, so Phase 1 is the first real compile.
