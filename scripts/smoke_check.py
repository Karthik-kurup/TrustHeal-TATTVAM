#!/usr/bin/env python3
"""
Smoke check script — verifies the running TrustHeal stack is healthy.
Usage: python scripts/smoke_check.py [--base-url http://localhost:8000]
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.request
import json


def get(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def check(label: str, fn) -> bool:  # type: ignore[type-arg]
    try:
        fn()
        print(f"  PASS  {label}")
        return True
    except Exception as exc:
        print(f"  FAIL  {label}: {exc}")
        return False


def run(base: str) -> bool:
    print(f"\nSmoke check against {base}\n")
    results = []

    def health_ok() -> None:
        data = get(f"{base}/api/health")
        assert data["status"] == "ok", f"expected ok, got {data['status']}"

    def openapi_schema() -> None:
        data = get(f"{base}/openapi.json")
        assert "openapi" in data
        assert "/api/health" in data.get("paths", {})

    def sim_state() -> None:
        data = get(f"{base}/api/sim/state")
        assert "status" in data
        assert "sim_time" in data

    def cluster_endpoint() -> None:
        data = get(f"{base}/api/cluster")
        assert "nodes" in data
        assert "services" in data

    def faults_list() -> None:
        data = get(f"{base}/api/faults/active")
        assert isinstance(data, list)

    def incidents_list() -> None:
        data = get(f"{base}/api/incidents")
        assert isinstance(data, list)

    def control_state() -> None:
        data = get(f"{base}/api/control/state")
        assert "autonomy_stage" in data
        assert "kill_switch" in data

    def models_status() -> None:
        data = get(f"{base}/api/models/status")
        assert "all_present" in data

    def compare_status() -> None:
        data = get(f"{base}/api/compare/status")
        assert isinstance(data, list)

    def audit_log() -> None:
        data = get(f"{base}/api/audit")
        assert isinstance(data, list)

    results.append(check("GET /api/health returns 200 + status=ok", health_ok))
    results.append(check("GET /openapi.json has /api/health path", openapi_schema))
    results.append(check("GET /api/sim/state has status + sim_time", sim_state))
    results.append(check("GET /api/cluster has nodes + services", cluster_endpoint))
    results.append(check("GET /api/faults/active returns list", faults_list))
    results.append(check("GET /api/incidents returns list", incidents_list))
    results.append(check("GET /api/control/state has autonomy_stage + kill_switch", control_state))
    results.append(check("GET /api/models/status returns artefact presence", models_status))
    results.append(check("GET /api/compare/status returns list", compare_status))
    results.append(check("GET /api/audit returns list", audit_log))

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} checks passed\n")
    return passed == total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TrustHeal smoke check")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--retry", type=int, default=3, help="Retry attempts")
    args = parser.parse_args()

    for attempt in range(args.retry):
        try:
            ok = run(args.base_url)
            sys.exit(0 if ok else 1)
        except Exception as exc:
            if attempt < args.retry - 1:
                print(f"Attempt {attempt + 1} failed ({exc}), retrying in 2s…")
                time.sleep(2)
            else:
                print(f"All {args.retry} attempts failed: {exc}")
                sys.exit(1)
