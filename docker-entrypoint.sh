#!/bin/sh
set -e

python -m pncp_query.services.runtime_checks
exec "$@"
