"""Live acceptance checks; save unmodified HTTP responses and browser screenshots."""

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
from playwright.sync_api import sync_playwright

OUT = Path("evidence/compose")
OUT.mkdir(parents=True, exist_ok=True)
client = httpx.Client(base_url="http://127.0.0.1", timeout=20)
results = {}


def call(method, path, expected=200, **kwargs):
    time.sleep(0.13)  # Keep ordinary checks below the configured 10 requests/s.
    response = client.request(method, path, **kwargs)
    print(f"{method} {path} -> {response.status_code} {response.text}", flush=True)
    assert response.status_code == expected, response.text
    return response.json()


def compose(*args):
    subprocess.run(["docker", "compose", *args], check=True)


def screenshot(page, url, name):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if ":3000/" in url:
        page.get_by_text("RPS — запросы в секунду", exact=True).wait_for(timeout=60000)
        page.locator("canvas").first.wait_for(timeout=60000)
        if name == "grafana-breaker-open":
            page.get_by_text("1", exact=True).first.wait_for(timeout=15000)
    elif "/docs/" in url:
        page.locator(".swagger-ui .opblock").first.wait_for(timeout=30000)
    elif "/trace/" in url:
        page.get_by_text("user-service", exact=False).first.wait_for(timeout=30000)
    else:
        page.get_by_text("order-service", exact=False).first.wait_for(timeout=30000)
    page.wait_for_timeout(3000)
    page.screenshot(path=str(OUT / (name + ".png")), full_page=True)


for base in [
    "http://127.0.0.1/health",
    "http://127.0.0.1:3000/api/health",
    "http://127.0.0.1:9090/-/ready",
    "http://127.0.0.1:16686/api/services",
]:
    for _ in range(90):
        try:
            if httpx.get(base).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("Not ready: " + base)

user = call("POST", "/api/users", 201, json={"email": uuid4().hex + "@example.com", "name": "Konstantin Malinin"})
uid = user["id"]
results["user_created"] = user
call("GET", f"/api/users/{uid}")
call("GET", "/api/users")
results["user_updated"] = call("PUT", f"/api/users/{uid}", json={"email": user["email"], "name": "Malinin K. N."})
call("POST", "/api/users", 400, json={"email": "bad", "name": ""})
call("POST", "/api/orders", 400, json={"user_id": uid, "total": -1})
call("POST", "/api/orders", 404, json={"user_id": 999999, "total": 10})
order = call("POST", "/api/orders", 201, json={"user_id": uid, "total": 1250.50})
results["order_created"] = order
call("GET", f"/api/orders/{order['id']}")
call("GET", "/api/orders")
call("GET", "/api/orders/999999", 404)
for _ in range(30):
    notifications = call("GET", "/api/notifications")
    if any(n["order_id"] == order["id"] for n in notifications):
        break
    time.sleep(1)
else:
    raise RuntimeError("OrderCreated was not consumed")
results["notifications"] = notifications
compose("restart", "user-service", "order-service", "notification-service")
time.sleep(10)
assert call("GET", f"/api/users/{uid}")["id"] == uid
assert call("GET", f"/api/orders/{order['id']}")["id"] == order["id"]
assert any(n["order_id"] == order["id"] for n in call("GET", "/api/notifications"))
results["persistence_after_restart"] = True

# Real hey traffic, through nginx and directly inside the Compose network.
with (OUT / "hey-gateway.txt").open("w") as stream:
    subprocess.run(["hey", "-n", "200", "-c", "20", "http://127.0.0.1/api/orders"], stdout=stream, check=True)
time.sleep(0.2)
with (OUT / "hey-load.txt").open("w") as stream:
    subprocess.run(["hey", "-z", "20s", "-c", "1", "-q", "5", "http://127.0.0.1/api/orders"], stdout=stream, check=True)
with ThreadPoolExecutor(max_workers=20) as pool:
    statuses = list(pool.map(lambda _: httpx.get("http://127.0.0.1/api/orders").status_code, range(40)))
assert 429 in statuses
results["rate_limit_statuses"] = {str(code): statuses.count(code) for code in set(statuses)}
print("RATE LIMIT:", results["rate_limit_statuses"], flush=True)

# Generate a visible monitoring interval while respecting the gateway limiter.
for _ in range(160):
    call("GET", "/api/orders")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1500, "height": 1000}, device_scale_factor=1)
    screenshot(page, "http://127.0.0.1/docs/users/docs", "swagger-users")
    screenshot(page, "http://127.0.0.1/docs/orders/docs", "swagger-orders")
    # Expanded, real Swagger Try-it-out execution.
    page.locator("#operations-orders-list_orders").click()
    page.get_by_role("button", name="Try it out").click()
    page.get_by_role("button", name="Execute", exact=True).click()
    page.wait_for_selector(".responses-table .response-col_status")
    page.screenshot(path=str(OUT / "swagger-execute.png"), full_page=True)

    # Stop upstream, establish 5 logical failures and prove fast OPEN rejection.
    before = len(call("GET", "/api/orders"))
    compose("stop", "user-service")
    failures = []
    for _ in range(5):
        start = time.monotonic()
        response = call("POST", "/api/orders", 503, json={"user_id": uid, "total": 20})
        failures.append({"response": response, "seconds": time.monotonic() - start})
    start = time.monotonic()
    fast = call("POST", "/api/orders", 503, json={"user_id": uid, "total": 20})
    elapsed = time.monotonic() - start
    health = call("GET", "/health/orders")
    assert health["circuit_breaker"] == "open", health
    assert len(call("GET", "/api/orders")) == before
    assert elapsed < 1, elapsed
    results["outage"] = {
        "failures": failures,
        "fast_rejection": fast,
        "seconds": elapsed,
        "health": health,
        "orders_unchanged": True,
    }
    print("BREAKER OPEN:", json.dumps(results["outage"]), flush=True)
    time.sleep(3)
    breaker_metric = httpx.get(
        "http://127.0.0.1:9090/api/v1/query", params={"query": 'circuit_breaker_state{service="order-service"}'}
    ).json()
    (OUT / "breaker-open-metric.json").write_text(json.dumps(breaker_metric, indent=2))
    assert breaker_metric["data"]["result"][0]["value"][1] == "1", breaker_metric
    screenshot(
        page, "http://127.0.0.1:3000/d/pr8/praktika?orgId=1&from=now-5m&to=now&refresh=5s&kiosk", "grafana-breaker-open"
    )
    # Save the real exported trace (shared trace id, both service resources).
    for _ in range(30):
        traces = httpx.get(
            "http://127.0.0.1:16686/api/traces",
            params={"service": "order-service", "limit": 100, "lookback": "1h", "operation": "POST /orders"},
        ).json()["data"]
        matches = [
            t
            for t in traces
            if {"user-service", "order-service"} <= {v["serviceName"] for v in t["processes"].values()}
        ]
        if matches:
            break
        time.sleep(1)
    (OUT / "jaeger-query.json").write_text(json.dumps(traces, indent=2))
    assert matches, "No actual cross-service trace"
    successful = [
        t
        for t in matches
        if any(
            tag["key"] == "http.status_code" and tag["value"] == 201
            for span in t["spans"]
            if span["operationName"] == "POST /orders"
            for tag in span["tags"]
        )
    ]
    assert successful, "No successful order creation trace"
    trace = successful[0]
    (OUT / "cross-service-trace.json").write_text(json.dumps(trace, indent=2))
    results["trace_id"] = trace["traceID"]
    screenshot(page, "http://127.0.0.1:16686/trace/" + trace["traceID"], "jaeger-trace")
    compose("start", "user-service")
    time.sleep(32)
    results["recovery_order"] = call("POST", "/api/orders", 201, json={"user_id": uid, "total": 33.33})
    assert call("GET", "/health/orders")["circuit_breaker"] == "closed"
    for _ in range(80):
        call("GET", "/api/orders")
    screenshot(
        page, "http://127.0.0.1:3000/d/pr8/praktika?orgId=1&from=now-5m&to=now&refresh=5s&kiosk", "grafana-recovery"
    )
    screenshot(page, "http://127.0.0.1:16686/search?service=order-service&lookback=1h&limit=20", "jaeger-search")
    browser.close()

# Complete user CRUD with a separate disposable row.
extra = call("POST", "/api/users", 201, json={"email": uuid4().hex + "@example.com", "name": "Delete test"})
time.sleep(0.15)
response = client.delete(f"/api/users/{extra['id']}")
assert response.status_code == 204
print("DELETE user -> 204", flush=True)
call("GET", f"/api/users/{extra['id']}", 404)
query = "sum(rate(api_requests_total[1m])) by (service)"
results["prometheus"] = httpx.get("http://127.0.0.1:9090/api/v1/query", params={"query": query}).json()
assert results["prometheus"]["data"]["result"]
(OUT / "verification.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
print(
    "PASS: CRUD, 400/404/503, restart persistence, RabbitMQ, 429, CB OPEN/recovery, metrics, cross-service trace",
    flush=True,
)
