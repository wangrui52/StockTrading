#!/bin/zsh

set -euo pipefail

project_root="${0:A:h:h}"
test_dir=$(mktemp -d "${TMPDIR:-/tmp}/stocktrading-docker-recovery.XXXXXX")
trap 'rm -r "$test_dir"' EXIT

mkdir -p "$test_dir/bin" "$test_dir/project/deploy"
cp "$project_root/start_local.command" "$test_dir/project/start_local.command"
chmod +x "$test_dir/project/start_local.command"
print 'APP_PORT=8080' > "$test_dir/project/.env.example"
touch "$test_dir/project/deploy/docker-compose.yml"

cat > "$test_dir/bin/docker" <<'EOF'
#!/bin/zsh
set -euo pipefail

print -r -- "$*" >> "$MOCK_DOCKER_LOG"

if [[ "$1" == info ]]; then
  [[ -f "$MOCK_DOCKER_READY" ]]
  exit $?
fi

if [[ "$1 $2" == "desktop status" ]]; then
  print 'Status running'
  exit 0
fi

if [[ "$1 $2" == "desktop restart" ]]; then
  touch "$MOCK_DOCKER_READY"
  exit 0
fi

if [[ "$1" == compose ]]; then
  exit 0
fi

exit 1
EOF

cat > "$test_dir/bin/curl" <<'EOF'
#!/bin/zsh
exit 0
EOF

cat > "$test_dir/bin/open" <<'EOF'
#!/bin/zsh
exit 0
EOF

cat > "$test_dir/bin/sleep" <<'EOF'
#!/bin/zsh
exit 0
EOF

chmod +x "$test_dir/bin/"*

export MOCK_DOCKER_LOG="$test_dir/docker.log"
export MOCK_DOCKER_READY="$test_dir/docker-ready"

if ! PATH="$test_dir/bin:/usr/bin:/bin" \
  START_LOCAL_NO_OPEN=1 \
  "$test_dir/project/start_local.command" > "$test_dir/output.log" 2>&1; then
  print -u2 '启动脚本未能从 Docker Engine 无响应状态恢复'
  cat "$test_dir/output.log" >&2
  exit 1
fi

grep -Fq 'desktop restart' "$MOCK_DOCKER_LOG" || {
  print -u2 'Docker Engine 无响应时没有重启 Docker Desktop'
  cat "$test_dir/output.log" >&2
  exit 1
}

grep -Fq 'Docker Desktop 已运行但引擎无响应' "$test_dir/output.log" || {
  print -u2 '缺少 Docker Engine 卡死恢复提示'
  cat "$test_dir/output.log" >&2
  exit 1
}

grep -Fq '启动成功：http://127.0.0.1:8080' "$test_dir/output.log" || {
  print -u2 'Docker Desktop 恢复后没有继续启动服务'
  cat "$test_dir/output.log" >&2
  exit 1
}

print 'Docker Desktop 卡死恢复契约通过'
