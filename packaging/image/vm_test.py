#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Boot-test the AI Linux reference image in QEMU (KVM if available, otherwise TCG) and validate it.

Scenario (each step recorded in the JSON report, nothing is assumed):
  1. boot (UEFI, optionally Secure Boot with Microsoft keys) → SSH → inventory, metrics, CLI, CPU inference
  2. A/B upgrade: stage the v2 rootfs from an attached drive → trial boot → health confirmation
  3. data persistence across the upgrade
  4. rollback to the previous slot
  5. failure: stage a broken rootfs → trial boot fails → automatic fallback to the default slot

Usage:
  python3 packaging/image/vm_test.py --disk out/ailx-1-vm.qcow2 --key vmkey/id_ed25519 \\
      --update out/rootfs-2-vm.ext4 --broken out/rootfs-broken.ext4 --out report/
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

OVMF = Path("/usr/share/OVMF")


class VM:
    def __init__(
        self, disk: Path, work: Path, key: Path, extra: list[Path], secureboot: bool, mem: int, smp: int, port: int
    ) -> None:
        self.work, self.key, self.port = work, key, port
        self.serial = work / "serial.log"
        overlay = work / "disk.qcow2"
        subprocess.run(
            [
                "qemu-img",
                "create",
                "-q",
                "-f",
                "qcow2",
                "-F",
                "qcow2" if disk.suffix == ".qcow2" else "raw",
                "-b",
                str(disk.resolve()),
                str(overlay),
            ],
            check=True,
        )
        code = OVMF / ("OVMF_CODE_4M.secboot.fd" if secureboot else "OVMF_CODE_4M.fd")
        vars_src = OVMF / ("OVMF_VARS_4M.ms.fd" if secureboot else "OVMF_VARS_4M.fd")
        shutil.copyfile(vars_src, work / "vars.fd")
        accel = "kvm" if os.access("/dev/kvm", os.W_OK) else "tcg,thread=multi"
        self.accel = accel.split(",")[0]
        machine = "q35,smm=on" if secureboot else "q35"
        self.cmd = [
            "qemu-system-x86_64",
            "-machine",
            machine,
            "-accel",
            accel,
            "-m",
            str(mem),
            "-smp",
            str(smp),
            "-cpu",
            "max" if self.accel == "tcg" else "host",
            "-no-user-config",
            "-nodefaults",
            "-display",
            "none",
            "-drive",
            f"if=pflash,format=raw,readonly=on,file={code}",
            "-drive",
            f"if=pflash,format=raw,file={work / 'vars.fd'}",
            "-drive",
            f"file={overlay},if=virtio,format=qcow2",
            "-netdev",
            f"user,id=n0,hostfwd=tcp:127.0.0.1:{port}-:22",
            "-device",
            "virtio-net-pci,netdev=n0",
            "-device",
            "i6300esb",
            "-action",
            "watchdog=reset",
            "-serial",
            f"file:{self.serial}",
            "-monitor",
            "none",
        ]
        if secureboot:
            self.cmd += ["-global", "driver=cfi.pflash01,property=secure,value=on"]
        for e in extra:
            self.cmd += ["-drive", f"file={e.resolve()},if=virtio,format=raw,readonly=on"]
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self) -> float:
        self.errlog = (self.work / "qemu.err").open("w")
        self.proc = subprocess.Popen(self.cmd, stdout=subprocess.DEVNULL, stderr=self.errlog)
        return time.time()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def ssh(self, cmd: str, timeout: float = 600) -> tuple[int, str]:
        full = [
            "ssh",
            "-i",
            str(self.key),
            "-p",
            str(self.port),
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "BatchMode=yes",
            "ailx-admin@127.0.0.1",
            cmd,
        ]
        try:
            r = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "timeout"
        return r.returncode, (r.stdout + r.stderr).strip()

    def wait_ssh(self, timeout: float = 1200) -> float:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(f"qemu exited with {self.proc.returncode}")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
                    s.settimeout(5)
                    if s.recv(16).startswith(b"SSH-"):
                        rc, _ = self.ssh("true", timeout=60)
                        if rc == 0:
                            return time.time() - t0
            except OSError:
                pass
            time.sleep(5)
        raise TimeoutError("SSH did not come up")

    def reboot(self, timeout: float = 1800) -> float:
        self.ssh("sudo systemctl reboot", timeout=30)
        t0 = time.time()
        while time.time() - t0 < 300:  # wait until the old system is gone
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s:
                    s.settimeout(3)
                    if not s.recv(16):
                        break
            except OSError:
                break
            time.sleep(3)
        time.sleep(10)
        return self.wait_ssh(timeout)

    def wait_health(self, timeout: float = 600) -> str:
        t0 = time.time()
        state = ""
        while time.time() - t0 < timeout:
            _, state = self.ssh(
                "systemctl show -p ActiveState --value ailx-boot-health.service; "
                "systemctl is-active ailx-boot-health.service",
                timeout=60,
            )
            if "inactive" in state or "active\nactive" in state or "failed" in state:
                return state
            time.sleep(10)
        return state


def jload(text: str) -> object:
    try:
        return json.loads(text)
    except ValueError:
        return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--disk", required=True, type=Path)
    ap.add_argument("--key", required=True, type=Path)
    ap.add_argument("--update", type=Path, help="rootfs image of the next version (attached as /dev/vdb)")
    ap.add_argument("--update-sha256")
    ap.add_argument("--broken", type=Path, help="rootfs image that cannot boot (attached as /dev/vdc)")
    ap.add_argument("--secureboot", action="store_true")
    ap.add_argument("--mem", type=int, default=2048)
    ap.add_argument("--smp", type=int, default=2)
    ap.add_argument("--port", type=int, default=2222)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--quick", action="store_true", help="only boot + inventory (no update/rollback scenario)")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    extra = [p for p in (a.update, a.broken) if p]
    vm = VM(a.disk, a.out, a.key, extra, a.secureboot, a.mem, a.smp, a.port)
    rep: dict[str, object] = {
        "accel": vm.accel,
        "secureboot": a.secureboot,
        "memory_mib": a.mem,
        "smp": a.smp,
        "disk": str(a.disk),
        "steps": {},
    }
    steps: dict[str, object] = rep["steps"]  # type: ignore[assignment]

    def run(name: str, cmd: str, timeout: float = 900) -> str:
        rc, out = vm.ssh(cmd, timeout)
        steps[name] = {"cmd": cmd, "rc": rc, "out": out[-6000:]}
        print(f"[{name}] rc={rc}\n{out[-1500:]}\n", flush=True)
        return out

    def ok(name: str) -> bool:
        st = steps.get(name)
        return isinstance(st, dict) and st.get("rc") == 0

    try:
        vm.start()
        rep["boot1_seconds_to_ssh"] = round(vm.wait_ssh(), 1)
        run(
            "wait-boot-finished",
            "timeout 600 sh -c 'until systemd-analyze time >/dev/null 2>&1; do sleep 5; done'; systemd-analyze time",
        )
        run("image-release", "cat /etc/ailx/image-release; uname -r")
        run(
            "secure-boot",
            "od -An -t u1 /sys/firmware/efi/efivars/SecureBoot-* 2>/dev/null | awk '{print \"SecureBoot=\" $NF}'; "
            "cat /sys/kernel/security/lockdown 2>/dev/null",
        )
        run("idle-memory", "sleep 60; free -m; cat /proc/meminfo | head -3")
        run(
            "services-running",
            "systemctl list-units --type=service --state=running --no-legend --plain | awk '{print $1}'",
        )
        run("timers", "systemctl list-timers --no-legend --plain | awk '{print $NF}'")
        run("failed-units", "systemctl --failed --no-legend --plain")
        run("watchdog-normal-boot", "ls /dev/watchdog* 2>&1; systemctl show -p RuntimeWatchdogUSec")
        run("packages", "dpkg-query -W | wc -l")
        run("disk-usage", "df -m / /data /boot/efi | tail -n +2; lsblk -o NAME,SIZE,LABEL,MOUNTPOINTS")
        run(
            "ssh-hardening",
            "sudo sshd -T | grep -E '^(passwordauthentication|permitrootlogin|kbdinteractiveauthentication|hostkey) '",
        )
        run("ailx-version", "ailx --version; ailx-bench --version")
        run("ailx-status", "ailx --no-nvml status")
        run("ailx-doctor", "ailx --no-nvml doctor; echo rc=$?")
        run("ailx-hardware", "ailx --no-nvml hardware")
        run("ailx-resources", "ailx --no-nvml resources")
        run("ailx-image-status", "ailx image status; sudo ailx image status")
        run("engine-verify", "ailx engine list; ailx engine verify llama.cpp")
        run(
            "cpu-inference-llama-bench",
            "B=$(ailx engine env | sed -n 's/^export AILX_LLAMA_BENCH=//p'); "
            "$B -m /var/lib/ailx/models/tiny-f16.gguf -p 32 -n 16 -r 2 -t 2 -o md 2>/dev/null",
        )
        run(
            "ailx-bench-campaign",
            "cd /tmp && cp -r /usr/share/ailx/workloads w && "
            "ailx-bench --store /tmp/store --no-nvml campaign all w/campaigns/synthetic-aa.json --report /tmp/aa.md "
            "| tail -4; ailx-bench --store /tmp/store store verify",
            timeout=1800,
        )
        run(
            "profile-apply-rollback",
            "sudo ailx profile apply inference --include-experimental --yes | tail -2; cat /proc/sys/vm/swappiness; "
            "sudo ailx rollback; cat /proc/sys/vm/swappiness",
        )
        if a.quick:
            return
        run(
            "persist-marker",
            "echo kept > /var/lib/ailx/models/marker && echo kept > ~/marker && "
            "cut -c1-40 /data/ssh/ssh_host_ed25519_key.pub",
        )
        if a.update:
            sha = f" --sha256 {a.update_sha256}" if a.update_sha256 else ""
            run("stage-update", f"sudo ailx image stage /dev/vdb{sha}", timeout=1800)
            run("armed", "sudo ailx image status")
            if not ok("stage-update"):
                rep["aborted"] = "stage-update failed; update/rollback/fallback scenario not executed"
                return
            rep["boot2_seconds_to_ssh"] = round(vm.reboot(), 1)
            steps["health-after-update"] = vm.wait_health()
            run(
                "after-update",
                "sudo ailx image status; cat /etc/ailx/image-release; ailx --version; "
                "cat /var/lib/ailx/models/marker ~/marker; cut -c1-40 /data/ssh/ssh_host_ed25519_key.pub; "
                "ailx engine verify llama.cpp; sudo journalctl -b -u ailx-boot-health --no-pager | tail -5; "
                "cat /sys/class/watchdog/watchdog0/identity /sys/class/watchdog/watchdog0/timeout; "
                "systemctl show -p RuntimeWatchdogUSec",
            )
            run("rollback", "sudo ailx image rollback")
            rep["boot3_seconds_to_ssh"] = round(vm.reboot(), 1)
            run("after-rollback", "sudo ailx image status; cat /etc/ailx/image-release")
        if a.broken:
            run("stage-broken", "sudo ailx image stage /dev/vdc", timeout=1800)
            if not ok("stage-broken"):
                rep["aborted"] = "stage-broken failed; fallback scenario not executed"
                return
            t0 = time.time()
            vm.ssh("sudo systemctl reboot", timeout=30)
            time.sleep(60)
            rep["fallback_seconds_to_ssh"] = round(vm.wait_ssh(2400) + 60, 1)
            rep["fallback_wall_seconds"] = round(time.time() - t0, 1)
            run("after-failed-trial", "sudo ailx image status; cat /etc/ailx/image-release; cat /proc/cmdline")
    finally:
        vm.stop()
        serial = vm.serial.read_text(errors="replace") if vm.serial.exists() else ""
        serial = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", serial)  # strip terminal control sequences
        rep["serial_markers"] = {
            "grub_slot_lines": re.findall(r"AI Linux: booting slot [AB] \(trial=[01]\)", serial),
            "kernel_panics": serial.count("Kernel panic"),
            "trial_watchdog_armed": re.findall(r"ailx: watchdog armed for trial boot \([^)]*\)", serial),
            "trial_watchdog_missing": serial.count("ailx: no watchdog device"),
            "init_missing": len(re.findall(r"requested /sbin/init|No init found|No working init", serial)),
        }
        (a.out / "report.json").write_text(json.dumps(rep, indent=2))
        print(json.dumps({k: v for k, v in rep.items() if k != "steps"}, indent=2))


if __name__ == "__main__":
    main()
