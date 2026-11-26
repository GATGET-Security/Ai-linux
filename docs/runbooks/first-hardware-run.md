# Runbook: First Hardware Run (Block 1.2 — HW-0 … HW-2 + E1)

> Status: ready to execute, **not yet executed** · Last updated: 2026-11-26 · Harness: `ailx-bench` 0.1.0 · Test plan: `docs/architecture/hardware-test-plan.md`

Reference platform (maintainer decision): **Ubuntu 26.04 LTS + NVIDIA** on the
workstation (Ryzen 9 7950X3D, RTX 4090, 64 GB DDR5, PCIe 5.0 NVMe). The
Proxmox server (i5-11500) is controller/CPU-only target. Nothing in this
runbook changes security settings (ADR-0004).

## 0. What this run will and will not show

- It **calibrates** the harness on real hardware (A/A noise, energy telemetry)
  and runs one first confirmatory experiment (E1, GPU power limit, H-S-01).
- It makes **no** claim about AI Linux being faster than Ubuntu — that needs
  the full U0/U1/U2/A protocol of `benchmarking.md`.
- Energy metrics cover the whole tool process (model load + llama-bench's
  internal warm-up + measurement). Identical work per run keeps arms
  comparable; absolute J/token values are therefore upper bounds.
- The wall meter is not a blocker (maintainer decision); without it, energy
  results are labeled `ground_truth: none` (software counters only).

## 1. Workstation setup (once)

```bash
# Ubuntu 26.04 LTS stock install, Secure Boot on; NVIDIA driver from the archive
sudo ubuntu-drivers install            # record the driver version it chose
sudo apt install -y git cmake build-essential python3-venv nvidia-cuda-toolkit
sudo reboot

git clone -b claude/ai-linux-research-architecture-1bc3lh https://github.com/GATGET-Security/Ai-linux.git
cd Ai-linux
python3 -m venv ~/.venvs/ailx && . ~/.venvs/ailx/bin/activate
pip install -e "python[nvidia,gguf]"

# llama.cpp, pinned to the commit used in CI, CUDA build
git clone https://github.com/ggml-org/llama.cpp ~/src/llama.cpp
git -C ~/src/llama.cpp checkout 98cc730cc8f8f798cf83d22c23a072c205b0e510
cmake -S ~/src/llama.cpp -B ~/src/llama.cpp/build-cuda -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
cmake --build ~/src/llama.cpp/build-cuda -j16 --target llama-bench llama-perplexity llama-quantize

# optional: CPU energy via the minimal privileged RAPL helper
sudo groupadd --system ailx && sudo usermod -aG ailx "$USER"   # re-login afterwards
sudo install -m 0755 helpers/rapl-collector/ailx_rapl_collector.py /usr/local/libexec/
sudo install -m 0644 helpers/rapl-collector/ailx-rapl-collector.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ailx-rapl-collector
```

Environment for all following steps:

```bash
. ~/.venvs/ailx/bin/activate
export AILX_STORE=~/ailx-store
export AILX_LLAMA_BENCH=~/src/llama.cpp/build-cuda/bin/llama-bench
export AILX_MODEL=/path/to/model.gguf   # operator's choice; note licence, precision, sha256
```

## 2. HW-0 inventory

```bash
ailx-bench probe --out hw0-probe.json        # lists every field it could NOT read, with the reason
ailx-bench telemetry check --seconds 5       # psi / cpu / nvml / rapl availability
nvidia-smi -q -d POWER,CLOCK,PERFORMANCE > hw0-nvidia.txt
lspci -vv -s "$(nvidia-smi --query-gpu=pci.bus_id --format=csv,noheader | sed 's/^0000//')" | grep -E 'LnkSta|BAR' > hw0-pcie.txt
```

Check manually and write into the validation ledger: memory is 2 DIMMs
(1DPC), EXPO state, PCIe Gen4 x16 on the GPU, ReBAR, V-Cache CCD CPU list
(`llc_domains` with the 96 MiB entry), BIOS version.

## 3. Freeze the environment for measurements

```bash
sudo systemctl stop unattended-upgrades apt-daily.timer apt-daily-upgrade.timer
# close desktop applications; keep the machine on the bench network only
```

## 4. HW-1 A/A calibration (GPU decode)

```bash
ailx-bench campaign all workloads/hw/campaign-gpu-aa.json --report aa-gpu.md
# note the printed analysis ID (ana-...) = CALIBRATION ID
ailx-bench validate-claims aa-gpu.md
```

Acceptance: no `improvement`/`regression` verdict in the A/A report; run-level
CV and noise floor recorded. Then size E1:

```bash
ailx-bench power --cv <run_cv of gpu_energy_j from aa-gpu.md> --effect 0.10 --e-min 0.05
```

If the required runs per arm exceed the 10 in the E1 template, raise
`design.blocks` in `workloads/hw/campaign-e1-gpu-power-limit.json` **before**
planning (pre-registration).

## 5. E1 — GPU power limit (H-S-01)

Allow exactly the two power-limit commands without a password (operator decision):

```bash
echo "$USER ALL=(root) NOPASSWD: /usr/bin/nvidia-smi -pl 450, /usr/bin/nvidia-smi -pl 315" | sudo tee /etc/sudoers.d/ailx-e1
ailx-bench campaign all workloads/hw/campaign-e1-gpu-power-limit.json --calibration <CALIBRATION ID> --report e1.md
sudo nvidia-smi -pl 450 && sudo rm /etc/sudoers.d/ailx-e1     # restore default and remove the rule
ailx-bench store verify
```

Read the verdicts literally: `no-detectable-difference` is **not**
"equivalent"; `equivalent-within-margin` requires the pre-registered 5% margin.

## 6. Optional: quality-gate reference for the chosen model

```bash
P=~/src/llama.cpp/build-cuda/bin/llama-perplexity
ailx-bench quality reference --binary $P --model $AILX_MODEL --corpus /path/to/eval-corpus.txt --out-dir ~/ailx-kld-ref --ctx 512 --chunks 64
ailx-bench quality evaluate  --binary $P --candidate /path/to/quantized-variant.gguf --reference ~/ailx-kld-ref/reference.json
```

## 7. Proxmox server (controller / CPU-only target)

```bash
# on the Proxmox host (record before/after in the ledger)
qm set <vmid> --cpu host --balloon 0 --memory 16384
systemctl disable --now ksmtuned
# inside the Ubuntu 26.04 VM
ailx-bench probe --out hw0-vm.json           # check that avx512 flags are visible (H-H-09)
ailx-bench campaign all workloads/campaigns/synthetic-aa.json --report vm-selftest.md   # simulated self-test
```

CPU-only llama-bench campaigns on the VM reuse `workloads/smoke/llama-bench-tiny.json`
as a template with a real CPU model.

## 8. After the run

1. `ailx-bench store verify`; archive `~/ailx-store` (local; sharing is opt-in).
2. Update `docs/state/validation-ledger.md` (rows VL-1.1-*) with result/analysis IDs.
3. Update `docs/state/hypotheses.md` (H-S-01, H-S-03, H-H-08 …) with verdicts and IDs.
4. Any number quoted in docs must carry its `ana-`/`res-` ID (`ailx-bench validate-claims`).
