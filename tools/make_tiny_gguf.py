# SPDX-License-Identifier: MIT
"""Create a tiny llama-architecture GGUF model with random weights for harness tests.

The model is a *test subject only*: it produces meaningless text. It exists so that
CI can run llama-bench and the KLD quality gate end-to-end without downloading
weights. Never use results obtained with it as statements about real models.

Usage:
  python tools/make_tiny_gguf.py --vocab <llama.cpp>/models/ggml-vocab-llama-spm.gguf --out tiny-f16.gguf
"""

from __future__ import annotations

import argparse

import numpy as np
from gguf import GGUFReader, GGUFValueType, GGUFWriter


def copy_tokenizer(reader: GGUFReader, writer: GGUFWriter) -> int:
    n_vocab = 0
    for field in reader.fields.values():
        if not field.name.startswith("tokenizer."):
            continue
        vtype = field.types[0]
        if vtype == GGUFValueType.ARRAY:
            sub = field.types[-1]
            if sub == GGUFValueType.STRING:
                values = [bytes(field.parts[i]).decode("utf-8") for i in field.data]
            else:
                values = [field.parts[i].tolist()[0] for i in field.data]
            writer.add_array(field.name, values)
            if field.name == "tokenizer.ggml.tokens":
                n_vocab = len(values)
        elif vtype == GGUFValueType.STRING:
            writer.add_string(field.name, bytes(field.parts[field.data[0]]).decode("utf-8"))
        else:
            value = field.parts[field.data[0]].tolist()[0]
            writer.add_key_value(field.name, value, vtype)
    return n_vocab


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocab", required=True, help="llama.cpp vocab-only GGUF (e.g. ggml-vocab-llama-spm.gguf)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-embd", type=int, default=128)
    ap.add_argument("--n-layer", type=int, default=2)
    ap.add_argument("--n-head", type=int, default=4)
    ap.add_argument("--n-ff", type=int, default=256)
    ap.add_argument("--ctx", type=int, default=512)
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    reader = GGUFReader(args.vocab)
    w = GGUFWriter(args.out, "llama")
    w.add_name("ailx-tiny-random-test-model")
    n_vocab = copy_tokenizer(reader, w)
    e, ff, hd = args.n_embd, args.n_ff, args.n_embd // args.n_head
    w.add_context_length(args.ctx)
    w.add_embedding_length(e)
    w.add_block_count(args.n_layer)
    w.add_feed_forward_length(ff)
    w.add_head_count(args.n_head)
    w.add_head_count_kv(args.n_head)
    w.add_rope_dimension_count(hd)
    w.add_layer_norm_rms_eps(1e-5)
    w.add_file_type(1)  # mostly F16

    def t(name: str, shape: tuple[int, ...], scale: float = 0.05, ones: bool = False) -> None:
        if ones:
            arr = np.ones(shape, dtype=np.float32)
        else:
            arr = (rng.standard_normal(shape) * scale).astype(np.float16)
        w.add_tensor(name, arr)

    t("token_embd.weight", (n_vocab, e))
    t("output_norm.weight", (e,), ones=True)
    t("output.weight", (n_vocab, e))
    for i in range(args.n_layer):
        p = f"blk.{i}."
        t(p + "attn_norm.weight", (e,), ones=True)
        t(p + "attn_q.weight", (e, e))
        t(p + "attn_k.weight", (e, e))
        t(p + "attn_v.weight", (e, e))
        t(p + "attn_output.weight", (e, e))
        t(p + "ffn_norm.weight", (e,), ones=True)
        t(p + "ffn_gate.weight", (ff, e))
        t(p + "ffn_up.weight", (ff, e))
        t(p + "ffn_down.weight", (e, ff))
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    print(f"wrote {args.out} (n_vocab={n_vocab}, n_embd={e}, n_layer={args.n_layer})")


if __name__ == "__main__":
    main()
