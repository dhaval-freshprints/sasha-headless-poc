"""Opt-in live checks. Calls the running web app and incurs agent API usage."""

import argparse
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen


def api(base_url, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(base_url + path, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8111")
    parser.add_argument("--output", default="runs/deal-manager-agent/live_checks.json")
    args = parser.parse_args()
    cases = []
    root = Path(__file__).with_name("test_results")
    for path in sorted(root.glob("*_sample.json")):
        fixture = json.loads(path.read_text())
        message = fixture["input"]
        expected = fixture.get("deal_id") or fixture["decision"]["deal_id"]
        payload = {name: message.get(name) or "" for name in ("sender_email", "subject", "body")}
        job = api(args.base_url, "/api/deal-manager/lookups", payload)
        cases.append({"case": path.stem, "lookup_id": job["lookup_id"], "expected": str(expected)})
        print("Submitted", path.stem, job["lookup_id"], flush=True)
    deadline = time.monotonic() + 3000
    pending = list(cases)
    while pending and time.monotonic() < deadline:
        for case in pending[:]:
            job = api(args.base_url, "/api/deal-manager/lookups/" + case["lookup_id"])
            if job["status"] not in {"completed", "failed"}:
                continue
            result = job.get("result") or {}
            case.update(status=job["status"], result=result, error=job.get("error"))
            case["passed"] = result.get("status") == "matched" and result.get("deal_id") == case["expected"]
            print(case["case"], "PASS" if case["passed"] else "FAIL", case.get("error"), flush=True)
            pending.remove(case)
        if pending:
            time.sleep(3)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(cases, indent=2))
    raise SystemExit(0 if all(case.get("passed") for case in cases) else 1)


if __name__ == "__main__":
    main()
