#!/usr/bin/env bash
# Master test suite runner for all AGY modules and launchers

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_DIR}"

echo "=============================================="
echo " Running AGY Modular Test Suites "
echo "=============================================="

PYTHONPATH="${REPO_DIR}" python3 -m unittest discover -s tests -p "test_*.py" -v

echo ""
echo "=============================================="
echo " Verifying All Launcher Executables "
echo "=============================================="

for launcher in bin/agy-*; do
    if [ -x "${launcher}" ]; then
        echo -n "Checking ${launcher} ... "
        "${launcher}" --probe >/dev/null 2>&1 || "${launcher}" --status >/dev/null 2>&1
        echo "OK"
    fi
done

echo ""
echo "All AGY tests and launcher validations passed successfully!"
