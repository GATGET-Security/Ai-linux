# SPDX-License-Identifier: MIT
"""Managed runtime: start/status/stop, admission control, priorities, preemption, placement, trust checks.

A stand-in ``llama-server`` (small HTTP server) is installed as an engine build so that process
handling is real; the llama.cpp end-to-end variant runs in the ``llamacpp`` CI job.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
import urllib.request
from pathlib import Path

import pytest
from compute_fix import workstation_caps
from test_compute_model_planner import write_model

from ailx.compute.runtime import AdmissionError, RuntimeError_, RuntimeManager, StartSpec, placement_cpus
from ailx.system.paths import Layout

GiB = 1024**3

FAKE_SERVER = textwrap.dedent(
    """\
    #!PYTHON
    import http.server, json, signal, sys
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1])
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/health" else 404); self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "argv": args}).encode())
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    signal.signal(signal.SIGTERM, lambda *a: (_ for _ in ()).throw(SystemExit(0)))
    print("fake llama-server listening", port, flush=True)
    srv.serve_forever()
    """
)


@pytest.fixture
def rt(desktop_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[RuntimeManager, dict, Path]:
    caps = workstation_caps(desktop_root)
    lay = Layout(
        root=desktop_root,
        state_dir=tmp_path / "state",
        engines_dir=desktop_root / "var/lib/ailx/engines",
        helper="/nonexistent",
        mode="user",
        models_dir=tmp_path / "models",
    )
    inst = lay.engines_dir / "llama.cpp" / "98cc730cc8f8-cuda"
    server = inst / "bin" / "llama-server"
    server.write_text(FAKE_SERVER.replace("#!PYTHON", "#!" + sys.executable))
    server.chmod(0o755)
    (inst / "manifest.json").write_text(
        json.dumps({"engine": "llama.cpp", "install_id": inst.name, "variant": "cuda", "kind": "cmake"})
    )
    link = lay.engines_dir / "llama.cpp" / "current"
    if link.is_symlink():
        link.unlink()
    link.symlink_to(inst.name)
    lay.models_dir.mkdir()
    write_model(lay.models_dir / "tiny.gguf")
    monkeypatch.delenv("AILX_RESERVE_RAM_MIB", raising=False)
    return RuntimeManager(lay), caps, lay.models_dir


def test_start_status_stop(rt: tuple[RuntimeManager, dict, Path]) -> None:
    mgr, caps, _ = rt
    rec = mgr.start(StartSpec(name="chat", model="tiny", ctx=256), caps)
    try:
        assert rec["status"] == "running" and rec["config_source"] == "engine-default"
        assert rec["cmd"][-4:] == ["-c", "256", "-np", "1"]  # engine fits layers itself (--fit default)
        assert mgr.wait_ready("chat", 30) == "ok"
        st = mgr.status("chat")
        assert st["live"]["health"] == "ok" and st["live"]["rss_bytes"] > 0
        assert st["admission"]["admitted"] and st["plan"]["mode"] == "gpu-full"
        with pytest.raises(RuntimeError_, match="already running"):
            mgr.start(StartSpec(name="chat", model="tiny"), caps)
    finally:
        out = mgr.stop("chat")
    assert out["status"] == "stopped" and mgr.status("chat")["live"]["health"] is None


def test_planned_config_passes_explicit_flags(rt: tuple[RuntimeManager, dict, Path]) -> None:
    mgr, caps, _ = rt
    prep = mgr.start(StartSpec(name="p", model="tiny", ctx=256, config="planned", threads=6), caps, dry_run=True)
    cmd = prep["cmd"]
    assert prep["status"] == "dry-run" and not mgr.instances()
    assert cmd[cmd.index("-ngl") + 1] == "3" and cmd[cmd.index("-t") + 1] == "6"  # 2 layers + output
    assert cmd[cmd.index("-fa") + 1] == "auto" and "-ctk" in cmd


def test_admission_budget_and_preemption(
    rt: tuple[RuntimeManager, dict, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr, caps, _ = rt
    bg = mgr.start(StartSpec(name="bg", model="tiny", ctx=256, priority="background"), caps)
    try:
        # shrink the RAM budget so that the second instance no longer fits beside the first
        need = bg["plan"]["host_bytes"]
        total = caps["memory"]["total_bytes"]
        mgr.cfg["reserve_ram_mib"] = (total - int(need * 1.5)) // 1024**2
        with pytest.raises(AdmissionError) as e:
            mgr.start(StartSpec(name="fg", model="tiny", ctx=256), caps)
        assert e.value.decision["preemptible"] == ["bg"] and "RAM budget" in e.value.decision["reasons"][0]
        rec = mgr.start(StartSpec(name="fg", model="tiny", ctx=256), caps, preempt=True)
        assert rec["admission"]["preempted"] == ["bg"]
        assert mgr.status("bg")["status"] == "stopped" and "preempted by fg" in mgr.load("bg")["stop_reason"]
        # an equal-priority instance is never preempted
        with pytest.raises(AdmissionError) as e2:
            mgr.start(StartSpec(name="fg2", model="tiny", ctx=256), caps, preempt=True)
        assert e2.value.decision["preemptible"] == []
    finally:
        for r in mgr.instances():
            if r["status"] == "running":
                mgr.stop(r["name"])


def test_priority_and_placement_are_per_process(rt: tuple[RuntimeManager, dict, Path]) -> None:
    mgr, caps, _ = rt
    cpus = sorted(os.sched_getaffinity(0))[:1]
    caps["cpu"]["llc_domains"][0]["cpus"] = cpus  # the fixture's CPU ids do not exist on the test host
    rec = mgr.start(StartSpec(name="pin", model="tiny", ctx=256, priority="batch", placement="llc:0"), caps)
    try:
        assert rec["placement"] == {"policy": "llc:0", "cpus": cpus, "experimental": True}
        st = mgr.status("pin")
        assert st["live"]["cpus_allowed"] == cpus
        assert Path(f"/proc/{rec['pid']}/oom_score_adj").read_text().strip() == "300"
        assert os.getpriority(os.PRIO_PROCESS, rec["pid"]) >= 5
    finally:
        mgr.stop("pin")


def test_placement_policies(desktop_root: Path) -> None:
    caps = workstation_caps(desktop_root)
    assert placement_cpus(caps, "none") is None
    assert placement_cpus(caps, "large-cache") == [*range(8), *range(16, 24)]
    assert placement_cpus(caps, "frequency") == [*range(8, 16), *range(24, 32)]
    assert placement_cpus(caps, "numa:0") == list(range(32))
    with pytest.raises(RuntimeError_):
        placement_cpus(caps, "llc:7")


def test_tampered_record_cannot_redirect_stop(rt: tuple[RuntimeManager, dict, Path]) -> None:
    mgr, caps, _ = rt
    rec = mgr.start(StartSpec(name="t", model="tiny", ctx=256), caps)
    try:
        p = mgr.dir / "instances" / "t.json"
        bad = json.loads(p.read_text())
        bad["pid"] = os.getpid()  # point the record at an unrelated process (this test runner)
        p.write_text(json.dumps(bad))
        assert mgr.stop("t")["status"] == "stopped"  # recorded as stopped, test runner untouched
        p.write_text(json.dumps(rec))
    finally:
        mgr.stop("t")
    assert mgr.refresh(mgr.load("t"))["status"] == "stopped"


def test_server_answers(rt: tuple[RuntimeManager, dict, Path]) -> None:
    mgr, caps, _ = rt
    rec = mgr.start(StartSpec(name="api", model="tiny", ctx=256), caps)
    try:
        assert mgr.wait_ready("api", 30) == "ok"
        with urllib.request.urlopen(f"http://127.0.0.1:{rec['port']}/health", timeout=5) as r:
            assert json.loads(r.read())["status"] == "ok"
        assert "listening" in mgr.logs("api")
    finally:
        mgr.stop("api")
    mgr.remove("api")
    assert mgr.instances() == []


def test_tuned_settings_are_validated(rt: tuple[RuntimeManager, dict, Path]) -> None:
    mgr, caps, _ = rt
    bad = [
        {"flash_attn": "on --host 0.0.0.0"},  # argument smuggling attempt
        {"n_gpu_layers": "99; rm -rf /"},
        {"unknown_flag": 1},
        {"cache_type_k": "q4_0"},  # lossy without explicit opt-in
    ]
    for tuned in bad:
        with pytest.raises(RuntimeError_):
            mgr.start(StartSpec(name="v", model="tiny", ctx=256, config="tuned", tuned=tuned), caps, dry_run=True)
    ok = mgr.start(
        StartSpec(name="v", model="tiny", ctx=256, config="tuned", tuned={"cache_type_k": "q8_0"}, allow_lossy=True),
        caps,
        dry_run=True,
    )
    assert ok["cmd"][-2:] == ["-ctk", "q8_0"]
