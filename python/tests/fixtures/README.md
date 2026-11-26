# Test fixtures

| File | Origin |
|------|--------|
| `llama-bench-98cc730.json` | **Captured** from `llama-bench` (llama.cpp commit 98cc730, CPU build) running the tiny random-weight test model from `tools/make_tiny_gguf.py`; paths and CPU name redacted. Not a model benchmark. |
| `llama-perplexity-kld-98cc730.txt` | **Captured** statistics block of `llama-perplexity --kl-divergence` (same build), q4_0 vs f16 of the tiny random model. |
| `vllm-bench-serve-constructed.json` | **Constructed** (vLLM not runnable in the CPU-only build environment). Must be replaced by a captured file during hardware validation (validation ledger VL-1.1-03). |
