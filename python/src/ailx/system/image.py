# SPDX-License-Identifier: MIT
"""A/B system versions for the AI Linux reference image (experimental prototype, ADR-0012).

Disk layout: ESP (``AILX-ESP``) · root slot A (``ailx-rootA``) · root slot B (``ailx-rootB``) ·
data (``ailx-data``: /var/lib/ailx state, engines, models; /home; SSH host keys).

GRUB reads ``EFI/ubuntu/grubenv`` on the ESP:

* ``default_slot`` — the slot booted normally;
* ``next_slot`` — set by ``ailx image stage``; GRUB boots it **once** (it clears the variable before
  booting) with ``ailx.trial=1`` on the kernel command line. If that boot fails (panic → reboot,
  hang → power cycle) the next boot automatically uses ``default_slot`` again.
* A healthy trial boot is confirmed by ``ailx image health`` (boot service) or ``ailx image confirm``,
  which makes it the new ``default_slot``. ``ailx image rollback`` switches back to the other slot.

The data partition is never written by updates; slots contain only the OS.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

from ailx.core import Doc
from ailx.system.paths import Layout

GRUBENV_SIZE = 1024
GRUBENV_HEADER = "# GRUB Environment Block\n"
SLOTS = ("A", "B")


class ImageError(RuntimeError):
    pass


def read_grubenv(path: Path) -> dict[str, str]:
    text = path.read_text(errors="replace")
    if not text.startswith(GRUBENV_HEADER):
        raise ImageError(f"{path} is not a GRUB environment block")
    out = {}
    for line in text[len(GRUBENV_HEADER) :].splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k] = v
    return out


def write_grubenv(path: Path, env: dict[str, str]) -> None:
    body = GRUBENV_HEADER + "".join(f"{k}={v}\n" for k, v in sorted(env.items()) if v != "")
    if len(body) > GRUBENV_SIZE:
        raise ImageError("grubenv overflow")
    data = (body + "#" * (GRUBENV_SIZE - len(body))).encode()
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ImageError(f"{' '.join(cmd)} failed ({r.returncode}): {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def _size(p: Path) -> int:
    return p.stat().st_size if p.is_file() else int(_run(["blockdev", "--getsize64", str(p)]))


def other(slot: str) -> str:
    return "B" if slot == "A" else "A"


class ImageManager:
    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self.grubenv = layout.host("/boot/efi/EFI/ubuntu/grubenv")

    def cmdline(self) -> dict[str, str]:
        try:
            text = self.layout.host("/proc/cmdline").read_text()
        except OSError:
            return {}
        return dict(re.findall(r"(ailx\.[a-z]+)=(\S+)", text))

    def is_image(self) -> bool:
        # The kernel command line decides: the ESP is mounted umask=0077, so unprivileged users cannot
        # even see whether grubenv exists (found in the QEMU validation, see image-prototype-report.md).
        return "ailx.slot" in self.cmdline()

    def _env(self) -> dict[str, str] | None:
        """Boot environment, or ``None`` if only root may read it."""
        try:
            return read_grubenv(self.grubenv)
        except PermissionError:
            return None
        except FileNotFoundError as e:
            raise ImageError(f"booted with ailx.slot but {self.grubenv} is missing") from e

    def release(self) -> dict[str, str]:
        out = {}
        try:
            for line in self.layout.host("/etc/ailx/image-release").read_text().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    out[k] = v.strip().strip('"')
        except OSError:
            pass
        return out

    def device(self, slot: str) -> Path:
        """Slot device by GPT partition name (stable: overwriting the slot does not change it).

        The filesystem label is only a fallback: a freshly written slot carries the label of the update
        image until ``stage`` relabels it (found in the QEMU validation, image-prototype-report.md)."""
        for d in (f"/dev/disk/by-partlabel/ailx-root{slot}", f"/dev/disk/by-label/ailx-root{slot}"):
            if self.layout.host(d).exists():
                return self.layout.host(d)
        return self.layout.host(f"/dev/disk/by-partlabel/ailx-root{slot}")

    def slot_release(self, slot: str) -> dict[str, str]:
        """Read /etc/ailx/image-release from a slot without mounting it (debugfs, read-only)."""
        dev = self.device(slot)
        if not dev.exists() or not shutil.which("debugfs"):
            return {}
        r = subprocess.run(["debugfs", "-R", "cat /etc/ailx/image-release", str(dev)], capture_output=True, text=True)
        rel = {}
        for line in r.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                rel[k] = v.strip().strip('"')
        return rel

    def status(self) -> Doc:
        if not self.is_image():
            return {"image": False, "reason": "not booted from an AI Linux A/B image (no ailx.slot on cmdline)"}
        cmd = self.cmdline()
        cur = cmd["ailx.slot"]
        trial = cmd.get("ailx.trial") == "1"
        st: Doc = {"image": True, "current_slot": cur, "trial_boot": trial, "release": self.release()}
        env = self._env()
        if env is None:
            st["note"] = "boot environment and inactive slot are readable by root only (sudo ailx image status)"
            return st
        st.update(
            default_slot=env.get("default_slot", "A"),
            next_slot=env.get("next_slot") or None,
            last_trial=self._last_trial(env, cur, trial),
            other_slot={"slot": other(cur), "release": self.slot_release(other(cur))},
        )
        return st

    @staticmethod
    def _last_trial(env: dict[str, str], current: str, trial_boot: bool) -> dict[str, str]:
        lt = {k: env[k] for k in ("trial_slot", "trial_result") if env.get(k)}
        if lt.get("trial_result") == "pending" and not trial_boot:
            # GRUB marked the trial as pending, but we are not in that trial boot: it never completed
            lt["trial_result"] = f"failed (did not complete; fell back to slot {current})"
        return lt

    def _require_image(self) -> Doc:
        st = self.status()
        if not st["image"]:
            raise ImageError(st["reason"])
        if "default_slot" not in st:
            raise ImageError("changing the boot environment needs root")
        return st

    def stage(self, source: str, sha256: str | None = None, log=print) -> Doc:  # type: ignore[no-untyped-def]
        """Write a root filesystem image into the inactive slot and arm a one-time trial boot."""
        st = self._require_image()
        cur, target_slot = st["current_slot"], other(st["current_slot"])
        if not self.device(target_slot).exists():
            raise ImageError(f"slot {target_slot} not present")
        # resolve ONCE: by-label/by-partlabel links change while the slot is rewritten
        dev = Path(os.path.realpath(self.device(target_slot)))
        if self.device(cur).exists() and dev == Path(os.path.realpath(self.device(cur))):
            raise ImageError(f"slot {target_slot} resolves to the running slot {cur}; refusing")
        src = Path(source)
        if not src.exists():
            raise ImageError(f"{source} not found")
        label = _run(["e2label", str(src)]).strip()
        if label == f"ailx-root{cur}":  # would create a second filesystem GRUB could boot as the current slot
            raise ImageError(f"source carries the label of the running slot ({label}); refusing")
        size, dev_size = _size(src), _size(dev)
        if size > dev_size:
            raise ImageError(f"image ({size} B) larger than slot {target_slot} ({dev_size} B)")
        h = hashlib.sha256()
        with open(src, "rb") as fi, open(dev, "r+b") as fo:
            while chunk := fi.read(4 << 20):
                h.update(chunk)
                fo.write(chunk)
            fo.flush()
            os.fsync(fo.fileno())
        digest = h.hexdigest()
        if sha256 and digest != sha256:
            raise ImageError(f"sha256 mismatch: {digest} != {sha256}; slot {target_slot} NOT armed")
        _run(["e2fsck", "-fn", str(dev)])
        _run(["e2label", str(dev), f"ailx-root{target_slot}"])
        env = read_grubenv(self.grubenv)
        env.update(next_slot=target_slot, trial_slot="", trial_result="")
        write_grubenv(self.grubenv, env)
        log(f"slot {target_slot} written (sha256 {digest[:16]}…) and armed for ONE trial boot; reboot to try it")
        return {"slot": target_slot, "sha256": digest, "release": self.slot_release(target_slot)}

    def confirm(self) -> Doc:
        st = self._require_image()
        env = read_grubenv(self.grubenv)
        env.update(default_slot=st["current_slot"], next_slot="", trial_slot=st["current_slot"], trial_result="ok")
        write_grubenv(self.grubenv, env)
        return self.status()

    def rollback(self) -> Doc:
        st = self._require_image()
        target = other(st["current_slot"])
        if not self.device(target).exists():
            raise ImageError(f"slot {target} not present")
        env = read_grubenv(self.grubenv)
        env.update(default_slot=target, next_slot="")
        write_grubenv(self.grubenv, env)
        return {"default_slot": target, "note": "reboot to boot the other slot"}

    def health(self, checks: list[tuple[str, bool]]) -> Doc:
        """Called at boot. On a trial boot: confirm if all checks pass, otherwise record the failure."""
        st = self._require_image()
        if not st["trial_boot"]:
            return {"trial": False, "checks": checks}
        ok = all(passed for _, passed in checks)
        env = read_grubenv(self.grubenv)
        if ok:
            env.update(default_slot=st["current_slot"], trial_slot=st["current_slot"], trial_result="ok")
        else:
            env.update(trial_slot=st["current_slot"], trial_result="failed:" + ",".join(n for n, p in checks if not p))
        write_grubenv(self.grubenv, env)
        return {"trial": True, "confirmed": ok, "checks": checks}
