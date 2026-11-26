# ailx-rapl-collector

> Status: Phase 1.1 · Licence: MIT · Standard library only

Minimal privileged helper that lets the benchmark harness read CPU energy
(RAPL/powercap) **without** making `energy_uj` world-readable.

## Threat model

| Threat | Mitigation |
|--------|-----------|
| Power side channel (PLATYPUS, CVE-2020-8694/8695) via high-rate energy reads | Access only for root and one group (`ailx`); per-connection rate limit (default 100 ms); package/zone-level counters only; optional quantisation (`--quantum-uj`) |
| Privilege escalation through the helper | No write path, no command execution, no arguments from clients except the literal `read`; systemd hardening (`NoNewPrivileges`, empty capability set, `ProtectSystem=strict`, `PrivateNetwork`, `RestrictAddressFamilies=AF_UNIX`) |
| Tampering with permissions of powercap files | The helper never calls `chmod`/`chown` on sysfs; it only changes its own socket |
| Unauthorised local clients | Socket mode 0660 + `SO_PEERCRED` check (uid 0, primary or supplementary group) |

Residual risk: members of the `ailx` group can observe coarse package energy
at ≤ 10 Hz. Grant the group only to benchmark operators.

## Install (Ubuntu 26.04)

```bash
sudo groupadd --system ailx
sudo usermod -aG ailx "$USER"          # re-login afterwards
sudo install -m 0755 ailx_rapl_collector.py /usr/local/libexec/ailx_rapl_collector.py
sudo install -m 0644 ailx-rapl-collector.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ailx-rapl-collector
echo read | socat - UNIX-CONNECT:/run/ailx-rapl/rapl.sock   # optional check
```

The harness sampler `rapl` connects to `/run/ailx-rapl/rapl.sock`
(override with `AILX_RAPL_SOCKET`). Without the helper, the sampler reports
`available: false` and runs continue without CPU energy.
