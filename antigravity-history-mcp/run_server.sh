#!/bin/bash
# Antigravity History MCP Wrapper
# Ensures the server stays alive, handles stdio transport, and isolates dependencies.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
export PYTHONPATH="$DIR"

exec "$DIR/venv/bin/python3" "$DIR/server.py" "$@"
