# SPDX-License-Identifier: MIT
"""Header-only GGUF reader (format v2/v3) for model analysis.

Reads the key/value metadata and the tensor table, never tensor data, so analysing a 100 GB model
costs a few MB of I/O. Model files are untrusted input (overview.md, model-file security): every
count and length is bounded, the file is never executed or memory-mapped, and parse errors raise
``GGUFError`` instead of crashing. Large arrays (tokenizer vocabularies) are summarised by length.

Type sizes follow ggml (block size, bytes per block); they are cross-checked against the reference
``gguf`` Python package in the tests.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, BinaryIO

MAGIC = b"GGUF"
MAX_TENSORS = 1_000_000
MAX_KV = 100_000
MAX_STR = 16 << 20
MAX_ARRAY = 100_000_000
KEEP_ARRAY = 1024  # arrays up to this length are kept (e.g. per-layer head counts)

# ggml type id -> (name, block size, bytes per block)
GGML_TYPES: dict[int, tuple[str, int, int]] = {
    0: ("F32", 1, 4),
    1: ("F16", 1, 2),
    2: ("Q4_0", 32, 18),
    3: ("Q4_1", 32, 20),
    6: ("Q5_0", 32, 22),
    7: ("Q5_1", 32, 24),
    8: ("Q8_0", 32, 34),
    9: ("Q8_1", 32, 40),
    10: ("Q2_K", 256, 84),
    11: ("Q3_K", 256, 110),
    12: ("Q4_K", 256, 144),
    13: ("Q5_K", 256, 176),
    14: ("Q6_K", 256, 210),
    15: ("Q8_K", 256, 292),
    16: ("IQ2_XXS", 256, 66),
    17: ("IQ2_XS", 256, 74),
    18: ("IQ3_XXS", 256, 98),
    19: ("IQ1_S", 256, 50),
    20: ("IQ4_NL", 32, 18),
    21: ("IQ3_S", 256, 110),
    22: ("IQ2_S", 256, 82),
    23: ("IQ4_XS", 256, 136),
    24: ("I8", 1, 1),
    25: ("I16", 1, 2),
    26: ("I32", 1, 4),
    27: ("I64", 1, 8),
    28: ("F64", 1, 8),
    29: ("IQ1_M", 256, 56),
    30: ("BF16", 1, 2),
    34: ("TQ1_0", 256, 54),
    35: ("TQ2_0", 256, 66),
    39: ("MXFP4", 32, 17),
}

# GGUF metadata value types
_SCALAR = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}


class GGUFError(ValueError):
    pass


class _Reader:
    def __init__(self, f: BinaryIO, size: int) -> None:
        self.f, self.size = f, size

    def read(self, n: int) -> bytes:
        b = self.f.read(n)
        if len(b) != n:
            raise GGUFError("truncated GGUF header")
        return b

    def unpack(self, fmt: str) -> Any:
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))[0]

    def string(self) -> str:
        n = self.unpack("<Q")
        if n > MAX_STR:
            raise GGUFError(f"string length {n} exceeds limit")
        return self.read(n).decode("utf-8", errors="replace")

    def value(self, vtype: int) -> Any:
        if vtype in _SCALAR:
            return self.unpack(_SCALAR[vtype])
        if vtype == 8:
            return self.string()
        if vtype == 9:
            etype, n = self.unpack("<I"), self.unpack("<Q")
            if n > MAX_ARRAY:
                raise GGUFError(f"array length {n} exceeds limit")
            if n > KEEP_ARRAY:
                if etype in _SCALAR:
                    skip = n * struct.calcsize(_SCALAR[etype])
                    if self.f.tell() + skip > self.size:
                        raise GGUFError("truncated GGUF array")
                    self.f.seek(skip, 1)
                else:
                    for _ in range(n):
                        self.value(etype)
                return {"array_type": etype, "length": n}
            return [self.value(etype) for _ in range(n)]
        raise GGUFError(f"unknown metadata value type {vtype}")


def tensor_bytes(type_id: int, shape: list[int]) -> int:
    if type_id not in GGML_TYPES:
        raise GGUFError(f"unknown ggml type {type_id}")
    _, blk, size = GGML_TYPES[type_id]
    n = 1
    for d in shape:
        n *= d
    if n % blk:
        raise GGUFError(f"tensor with {n} elements is not a multiple of block size {blk}")
    return n // blk * size


def read_gguf(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    size = p.stat().st_size
    with open(p, "rb") as f:
        r = _Reader(f, size)
        if r.read(4) != MAGIC:
            raise GGUFError(f"{p.name}: not a GGUF file")
        version = r.unpack("<I")
        if version not in (2, 3):
            raise GGUFError(f"unsupported GGUF version {version}")
        n_tensors, n_kv = r.unpack("<Q"), r.unpack("<Q")
        if n_tensors > MAX_TENSORS or n_kv > MAX_KV:
            raise GGUFError("implausible tensor or metadata count")
        meta: dict[str, Any] = {}
        for _ in range(n_kv):
            key = r.string()
            meta[key] = r.value(r.unpack("<I"))
        tensors = []
        for _ in range(n_tensors):
            name = r.string()
            n_dims = r.unpack("<I")
            if n_dims > 8:
                raise GGUFError(f"tensor {name}: {n_dims} dimensions")
            shape = [r.unpack("<Q") for _ in range(n_dims)]
            type_id, offset = r.unpack("<I"), r.unpack("<Q")
            tensors.append(
                {
                    "name": name,
                    "shape": shape,
                    "type": GGML_TYPES.get(type_id, (f"type{type_id}", 0, 0))[0],
                    "type_id": type_id,
                    "offset": offset,
                    "bytes": tensor_bytes(type_id, shape),
                }
            )
        align = int(meta.get("general.alignment", 32))
        if align <= 0 or align & (align - 1):
            raise GGUFError(f"invalid alignment {align}")
        data_start = (f.tell() + align - 1) // align * align
    end = max((t["offset"] + t["bytes"] for t in tensors), default=0)
    if data_start + end > size:
        raise GGUFError(f"tensor data extends beyond the file ({data_start + end} > {size} bytes)")
    return {"version": version, "metadata": meta, "tensors": tensors, "data_offset": data_start, "file_bytes": size}
