"""Exercise real HTTP services and all three scaled order pods in a kind lab."""

import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

folder = Path("k8s/evidence")
folder.mkdir(exist_ok=True)
user_base = "http://127.0.0.1:15001"
order_base = "http://127.0.0.1:15002"
with httpx.Client(timeout=15) as client:
    for url in (user_base, order_base, "http://127.0.0.1:15003"):
        for attempt in range(60):
            try:
                if client.get(url + "/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Service not ready: " + url)
    response = client.post(
        user_base + "/users", json={"email": uuid4().hex + "@example.com", "name": "Kubernetes Student"}
    )
    assert response.status_code == 201, response.text
    user = response.json()
    orders = []
    for total in (100.50, 200.25, 300):
        response = client.post(order_base + "/orders", json={"user_id": user["id"], "total": total})
        assert response.status_code == 201, response.text
        orders.append(response.json())
    print("Three HTTP orders created:", [o["id"] for o in orders], flush=True)
    pods = json.loads(
        subprocess.check_output(
            ["kubectl", "get", "pods", "-n", "pr8", "-l", "app=order-service", "-o", "json"], text=True
        )
    )["items"]
    assert len(pods) == 3
    observed = {}
    for pod in pods:
        name = pod["metadata"]["name"]
        command = [
            "kubectl",
            "exec",
            "-n",
            "pr8",
            name,
            "--",
            "python",
            "-c",
            "import urllib.request;print(urllib.request.urlopen('http://localhost:5002/orders').read().decode())",
        ]
        print("$ " + " ".join(command), flush=True)
        rows = json.loads(subprocess.check_output(command, text=True))
        ids = [row["id"] for row in rows if row["user_id"] == user["id"]]
        assert sorted(ids) == sorted(o["id"] for o in orders)
        observed[name] = ids
        print(name, "sees orders", ids, flush=True)
    for _ in range(60):
        rows = client.get("http://127.0.0.1:15003/notifications").json()
        if len([row for row in rows if row["user_id"] == user["id"]]) >= 3:
            break
        time.sleep(1)
    else:
        raise RuntimeError("Notifications did not arrive")
    result = {"user": user, "orders": orders, "replicas": observed, "notifications": rows}
    (folder / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print("PASS: 3 Ready replicas, shared SQLite PVC, HTTP create/read and RabbitMQ notifications", flush=True)
