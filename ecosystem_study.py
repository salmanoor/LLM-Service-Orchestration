"""Launch and evaluate five independent local HTTP service processes."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "outputs" / "study_results.json"
CAPABILITIES = ("measure", "segment", "compare", "rule", "provenance")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def spawn(name: str, port: int, fault: str = "none") -> subprocess.Popen:
    command = [sys.executable, str(ROOT / "service_runtime.py"), "--name", name, "--port", str(port), "--report", str(REPORT), "--fault", fault]
    return subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def checked_call(url: str, payload: dict, timeout: float = 1.0) -> tuple[str, float, dict | None]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(1_000_000)
            data = json.loads(body)
            if response.status != 200 or not isinstance(data, dict) or set(data) != {"service", "version", "dataset", "result"}:
                return "invalid_response", (time.perf_counter() - start) * 1000, None
            return "ok", (time.perf_counter() - start) * 1000, data
    except urllib.error.HTTPError as error:
        return f"http_{error.code}", (time.perf_counter() - start) * 1000, None
    except json.JSONDecodeError:
        return "invalid_response", (time.perf_counter() - start) * 1000, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return "transport_or_schema_error", (time.perf_counter() - start) * 1000, None


def wait_ready(name: str, port: int, process: subprocess.Popen) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{name} service exited during startup")
        try:
            with urllib.request.urlopen(url, timeout=0.3) as response:
                info = json.load(response)
            if info.get("service") == name and info.get("status") == "ready":
                return
        except Exception:
            time.sleep(0.05)
    raise TimeoutError(f"{name} service did not become healthy")


def test_rejection(base_url: str, path: str, payload: dict, expected: str) -> dict:
    status, _, _ = checked_call(base_url + path, payload)
    passed = status == expected
    return {"case": expected, "observed": status, "passed": passed}


def run() -> dict:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    processes: list[subprocess.Popen] = []
    registry = {}
    latency: dict[str, list[float]] = {name: [] for name in CAPABILITIES}
    trace = []
    reliability = []
    security = []
    try:
        for name in CAPABILITIES:
            port = free_port()
            process = spawn(name, port)
            processes.append(process)
            registry[name] = {"url": f"http://127.0.0.1:{port}/v1/{name}", "port": port, "process_id": process.pid}
        for name in CAPABILITIES:
            wait_ready(name, registry[name]["port"], next(p for p in processes if p.pid == registry[name]["process_id"]))
        (ROOT / "outputs" / "service_registry.json").write_text(json.dumps(registry, indent=2), encoding="utf-8")
        for name in CAPABILITIES:
            for _ in range(5):
                status, elapsed, data = checked_call(registry[name]["url"], {"dataset": "aalto_hall101"})
                if status != "ok" or data["service"] != name:
                    raise AssertionError(f"{name} contract failure: {status}")
                latency[name].append(elapsed)
        # Exercise the same 25 prompt decisions against real HTTP service processes.
        for case in report["planner_evaluation"]["cases"]:
            selected = case["agreement_guard_tool"]
            status, elapsed, data = checked_call(registry[selected]["url"], {"dataset": "aalto_hall101"})
            trace.append({"expected": case["expected"], "selected": selected, "service_status": status, "service_ms": round(elapsed, 3), "response_service": data["service"] if data else None})
        if len(trace) != 25 or any(row["service_status"] != "ok" for row in trace):
            raise AssertionError("Planner-to-service integration failed")
        base = registry["measure"]["url"].rsplit("/v1/", 1)[0]
        security.extend([
            test_rejection(base, "/v1/admin", {"dataset": "aalto_hall101"}, "http_404"),
            test_rejection(base, "/v1/measure", {"dataset": "../../models"}, "http_400"),
            test_rejection(base, "/v1/measure", {"dataset": "aalto_hall101", "command": "print secrets"}, "http_400"),
        ])
        oversized = urllib.request.Request(base + "/v1/measure", data=b"x" * 5000, headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(oversized, timeout=1)
            observed = "unexpected_success"
        except urllib.error.HTTPError as error:
            observed = f"http_{error.code}"
        security.append({"case": "http_413", "observed": observed, "passed": observed == "http_413"})

        # Fault injection is configured at process launch, never from client input.
        port = free_port(); fault_process = spawn("rule", port, "unavailable"); processes.append(fault_process); wait_ready("rule", port, fault_process)
        status, _, _ = checked_call(f"http://127.0.0.1:{port}/v1/rule", {"dataset": "aalto_hall101"})
        reliability.append({"fault": "HTTP 503 unavailable", "observed": status, "failed_closed": status == "http_503"})
        stop(fault_process); processes.remove(fault_process)

        port = free_port(); fault_process = spawn("rule", port, "malformed"); processes.append(fault_process); wait_ready("rule", port, fault_process)
        status, _, _ = checked_call(f"http://127.0.0.1:{port}/v1/rule", {"dataset": "aalto_hall101"})
        reliability.append({"fault": "malformed JSON response", "observed": status, "failed_closed": status == "invalid_response"})
        stop(fault_process); processes.remove(fault_process)

        port = free_port(); fault_process = spawn("rule", port, "slow"); processes.append(fault_process); wait_ready("rule", port, fault_process)
        status, _, _ = checked_call(f"http://127.0.0.1:{port}/v1/rule", {"dataset": "aalto_hall101"}, timeout=0.1)
        reliability.append({"fault": "1 s slow service / 100 ms client timeout", "observed": status, "failed_closed": status == "transport_or_schema_error"})
        stop(fault_process); processes.remove(fault_process)

        # Recovery: healthy replacement is discoverable and returns a valid contract.
        port = free_port(); recovered = spawn("rule", port); processes.append(recovered); wait_ready("rule", port, recovered)
        status, _, _ = checked_call(f"http://127.0.0.1:{port}/v1/rule", {"dataset": "aalto_hall101"})
        reliability.append({"fault": "replacement process recovery", "observed": status, "failed_closed": status == "ok"})
        if not all(row["passed"] for row in security) or not all(row["failed_closed"] for row in reliability):
            raise AssertionError(json.dumps({"security": security, "reliability": reliability}))
    finally:
        for process in reversed(processes):
            stop(process)

    summary = {
        "architecture": "five independently launched Python processes on loopback HTTP; static allow-listed capability registry; JSON contracts",
        "service_count": len(registry), "planner_cases_dispatched": len(trace),
        "planner_dispatch_success_rate": sum(x["service_status"] == "ok" for x in trace) / max(1, len(trace)),
        "service_latency_ms": {name: {"median": sorted(vals)[len(vals)//2], "p95": sorted(vals)[math.ceil(.95*len(vals))-1]} for name, vals in latency.items()},
        "security_rejections": security, "fault_injection": reliability,
        "note": "Local process-boundary experiment, not a multi-host or production security certification.",
        "trace": trace,
    }
    out = ROOT / "outputs" / "ecosystem_study.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    result = run()
    print(json.dumps({k: v for k, v in result.items() if k != "trace"}, indent=2))
