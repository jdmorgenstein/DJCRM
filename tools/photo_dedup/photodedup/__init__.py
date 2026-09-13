"""Cross-library photo de-duplication for merging Google Photos into iCloud.

The problem this package solves: the same photograph stored in iCloud and in
Google Photos almost never has the same file hash.  Google rewrites metadata on
Takeout export, may transcode HEIC to JPEG, and re-encodes anything uploaded in
"Storage saver" quality.  Identity therefore has to be established from the
*capture*, not from the bytes.

See README.md for the full playbook.
"""

__version__ = "1.0.0"
