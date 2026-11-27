# SPDX-License-Identifier: MIT
"""AI Compute Engine: hardware capabilities, workload intelligence, runtime and autotuning.

Layering (each module only uses the ones above it):

* ``capabilities`` / ``providers`` — what the machine can do (read-only, unprivileged).
* ``gguf`` / ``model`` — what a model needs (header-only parsing, estimates labeled as such).
* ``planner`` — placement, engine selection and bottleneck analysis (capabilities × model).
* ``runtime`` — managed inference processes with admission control and CPU placement.
* ``tuning`` — candidate generation and adoption on top of the existing campaign/statistics/
  quality-gate/store machinery (no second measurement framework).

Nothing in this package changes host configuration; host knobs stay in ``ailx.system``.
"""
