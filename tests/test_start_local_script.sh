#!/bin/zsh

set -euo pipefail

project_root="${0:A:h:h}"
script_path="$project_root/start_local.command"

[[ -f "$script_path" ]] || { print -u2 "start_local.command 不存在"; exit 1; }
[[ -x "$script_path" ]] || { print -u2 "start_local.command 不可执行"; exit 1; }

zsh -n "$script_path"

required_patterns=(
  'script_dir="${0:A:h}"'
  'docker info'
  'open -a Docker'
  'docker compose -f deploy/docker-compose.yml up --build -d'
  'python -m scripts.seed_demo'
  '/api/v1/health'
  'START_LOCAL_NO_OPEN'
)

for pattern in $required_patterns; do
  grep -Fq "$pattern" "$script_path" || {
    print -u2 "启动脚本缺少契约: $pattern"
    exit 1
  }
done

print "启动脚本静态契约通过"
