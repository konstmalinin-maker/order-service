#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
openapi-python-client generate --path docs/user-openapi.json --meta none --output-path user_contract_client --overwrite --fail-on-warning
