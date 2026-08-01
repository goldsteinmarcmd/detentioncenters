#!/bin/zsh
set -euo pipefail

repo_dir="${0:A:h:h}"
python_bin="$repo_dir/.venv/bin/python"

if [[ ! -x "$python_bin" ]]; then
  print -u2 "Missing $python_bin. Run scripts/bootstrap-worker.sh first."
  exit 2
fi

cd "$repo_dir"
exec "$python_bin" -m pipeline.refresh --publish
