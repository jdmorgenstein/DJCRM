"""Content fingerprints that survive re-encoding and metadata rewrites.

Three levels, deliberately, because each catches a different failure mode:

``file_sha256``
    The bytes.  Only matches when nothing at all was touched.
``pixel_sha256``
    SHA-256 of the decoded, orientation-normalised RGB buffer.  Matches when
    Google handed back the original picture but rewrote or stripped the
    metadata -- the single most common reason two "identical" photos hash
    differently.  Exact: no false positives.
``phash`` / ``dhash``
    Perceptual, 64-bit.  Match across HEIC->JPEG transcodes, "Storage saver"
    re-compression and downscaling, where the pixels genuinely differ.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

from PIL import Image, ImageFile, ImageOps

try:  # HEIC/HEIF is the default iPhone format; without this they cannot decode.
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:  # pragma: no cover - depends on the install
    HEIF_SUPPORTED = False

try:
    import numpy
except ImportError:  # pragma: no cover - optional accelerator
    numpy = None

# Photos that came back slightly truncated from a Takeout zip are still worth
# fingerprinting; a partial decode beats dropping the file from the index.
ImageFile.LOAD_TRUNCATED_IMAGES = True

Image.MAX_IMAGE_PIXELS = None  # personal libraries contain legitimate panoramas

DCT_SIZE = 32
DCT_KEEP = 8
DHASH_SIZE = 8
HASH_BITS = 64


def _cosine_table(n: int) -> list[list[float]]:
    return [[math.cos(math.pi * (x + 0.5) * k / n) for x in range(n)] for k in range(n)]


_COS = _cosine_table(DCT_SIZE)
_COS_NP = numpy.array(_COS) if numpy is not None else None


def file_sha256(path: str | Path, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of the raw file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def decode(path: str | Path) -> Image.Image:
    """Open an image with its EXIF orientation flag baked into the pixels.

    Normalising orientation matters because Google sometimes materialises the
    rotation that the iPhone only recorded as a flag.
    """
    image = Image.open(path)
    image.load()
    return ImageOps.exif_transpose(image) or image


def pixel_sha256(image: Image.Image) -> str:
    """SHA-256 of the decoded RGB buffer, dimensions included."""
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    digest = hashlib.sha256()
    digest.update(f"{rgb.width}x{rgb.height}|".encode("ascii"))
    digest.update(rgb.tobytes())
    return digest.hexdigest()


def _dct2(matrix: list[list[float]]) -> list[list[float]]:
    """Separable 2-D DCT-II.  Unnormalised: the hash only compares magnitudes."""
    n = DCT_SIZE
    if numpy is not None:
        block = numpy.array(matrix, dtype=numpy.float64)
        return (_COS_NP @ block @ _COS_NP.T).tolist()

    rows = [[sum(row[x] * _COS[j][x] for x in range(n)) for j in range(n)] for row in matrix]
    return [[sum(rows[y][j] * _COS[k][y] for y in range(n)) for j in range(n)] for k in range(n)]


def greyscale(image: Image.Image) -> Image.Image:
    """One shared luminance pass; both perceptual hashes resize from it.

    Always taken from the full-resolution image rather than from a cheap
    pre-reduction, so a 12 MP original and a 2 MP re-encode of it go through
    exactly the same filter and land on the same hash.
    """
    return image if image.mode == "L" else image.convert("L")


def perceptual(image: Image.Image) -> tuple[int, int]:
    """``(phash, dhash)`` computed from a single greyscale conversion."""
    grey = greyscale(image)
    return phash(grey), dhash(grey)


def phash(image: Image.Image) -> int:
    """64-bit DCT perceptual hash (the classic pHash construction)."""
    small = greyscale(image).resize((DCT_SIZE, DCT_SIZE), Image.Resampling.LANCZOS)
    pixels = small.tobytes()  # mode "L": one byte per pixel, row-major
    matrix = [list(pixels[row * DCT_SIZE:(row + 1) * DCT_SIZE]) for row in range(DCT_SIZE)]
    coefficients = _dct2(matrix)

    low = [coefficients[k][j] for k in range(DCT_KEEP) for j in range(DCT_KEEP)]
    # The DC term dwarfs everything else, so it is excluded from the median.
    rest = sorted(low[1:])
    median = rest[len(rest) // 2]

    bits = 0
    for value in low:
        bits = (bits << 1) | (1 if value > median else 0)
    return bits


def dhash(image: Image.Image) -> int:
    """64-bit horizontal gradient hash.  Cheap, and fails differently to pHash."""
    small = greyscale(image).resize((DHASH_SIZE + 1, DHASH_SIZE), Image.Resampling.LANCZOS)
    pixels = small.load()
    bits = 0
    for y in range(DHASH_SIZE):
        for x in range(DHASH_SIZE):
            bits = (bits << 1) | (1 if pixels[x, y] < pixels[x + 1, y] else 0)
    return bits


_MASK = (1 << HASH_BITS) - 1


def to_signed(value: int) -> int:
    """Fold a 64-bit hash into SQLite's signed INTEGER range."""
    value &= _MASK
    return value - (1 << HASH_BITS) if value >> (HASH_BITS - 1) else value


def to_unsigned(value: int) -> int:
    """Inverse of :func:`to_signed`."""
    return value & _MASK


def hamming(left: int, right: int) -> int:
    """Number of differing bits.  Accepts signed or unsigned representations."""
    return ((left ^ right) & _MASK).bit_count()


def bands(value: int, count: int) -> list[tuple[int, int]]:
    """Split a 64-bit hash into ``count`` (index, chunk) pairs for blocking.

    Pigeonhole principle: if two hashes differ in at most ``count - 1`` bits
    then at least one band is bit-identical.  Indexing the bands therefore
    gives an *exact* candidate set for a Hamming threshold of ``count - 1``,
    without comparing every pair.
    """
    if count < 1:
        raise ValueError("count must be >= 1")

    value &= _MASK
    result = []
    consumed = 0
    for index in range(count):
        width = (HASH_BITS - consumed) // (count - index)
        chunk = (value >> (HASH_BITS - consumed - width)) & ((1 << width) - 1)
        result.append((index, chunk))
        consumed += width
    return result
