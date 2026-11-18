"""Copy versioned configuration into named Docker volumes (no host bind assumptions)."""

import shutil
from pathlib import Path

for src, dst in [
    ("deploy/nginx/nginx.conf", "/config/nginx/nginx.conf"),
    ("deploy/prometheus/prometheus.yml", "/config/prometheus/prometheus.yml"),
    ("deploy/grafana/dashboard.json", "/config/dashboards/dashboard.json"),
]:
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
shutil.copytree("deploy/grafana/provisioning", "/config/grafana", dirs_exist_ok=True)
for path in Path("/config").rglob("*"):
    if path.is_symlink():
        continue
    path.chmod(0o755 if path.is_dir() else 0o644)
print("Configuration volumes initialized")
