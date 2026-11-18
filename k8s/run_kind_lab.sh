#!/usr/bin/env bash
set -euo pipefail
mkdir -p k8s/evidence
exec > >(tee k8s/evidence/kind-lab.log) 2>&1
set -x
kind create cluster --name pr8-lab --image kindest/node:v1.34.0 --wait 180s
USER_SERVICE_DIR=${USER_SERVICE_DIR:-../user-service}
docker build -t pr8-user-service:local "$USER_SERVICE_DIR"
docker build -t pr8-order-service:local .
kind load docker-image --name pr8-lab pr8-user-service:local pr8-order-service:local
kubectl apply -f k8s/services.yaml
kubectl rollout status -n pr8 deployment/rabbitmq --timeout=240s
kubectl rollout status -n pr8 deployment/user-service --timeout=240s
kubectl rollout status -n pr8 deployment/order-service --timeout=240s
kubectl rollout status -n pr8 deployment/notification-service --timeout=240s
kubectl scale -n pr8 deployment order-service --replicas=3
kubectl rollout status -n pr8 deployment/order-service --timeout=240s
kubectl get -n pr8 deployments,pods,services,pvc -o wide | tee k8s/evidence/scaled-resources.txt
kubectl port-forward -n pr8 service/user-service 15001:5001 > k8s/evidence/port-forward-users.log 2>&1 &
PF_USERS=$!
kubectl port-forward -n pr8 service/order-service 15002:5002 > k8s/evidence/port-forward-orders.log 2>&1 &
PF_ORDERS=$!
kubectl port-forward -n pr8 service/notification-service 15003:5003 > k8s/evidence/port-forward-notify.log 2>&1 &
PF_NOTIFY=$!
trap 'kill "$PF_USERS" "$PF_ORDERS" "$PF_NOTIFY" 2>/dev/null || true' EXIT
python k8s/verify_cluster.py
kubectl get -n pr8 deployment order-service -o json > k8s/evidence/order-deployment.json
kubectl get -n pr8 pods -o json > k8s/evidence/pods.json
