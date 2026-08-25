#!/bin/zsh

set -euo pipefail

script_dir="${0:A:h}"
cd "$script_dir"

fail() {
  print -u2 "\n启动失败：$1"
  print -u2 "请保留本窗口中的错误信息以便排查。"
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "未找到 Docker，请先安装 Docker Desktop。"
command -v curl >/dev/null 2>&1 || fail "未找到 curl。"

docker_is_ready() {
  docker info >/dev/null 2>&1 &
  local docker_check_pid=$!
  for check in {1..5}; do
    if ! kill -0 "$docker_check_pid" >/dev/null 2>&1; then
      if wait "$docker_check_pid"; then
        return 0
      fi
      return 1
    fi
    sleep 0.2
  done
  kill "$docker_check_pid" >/dev/null 2>&1 || true
  wait "$docker_check_pid" >/dev/null 2>&1 || true
  return 1
}

if ! docker_is_ready; then
  print "Docker Desktop 尚未运行，正在启动…"
  open -a Docker || fail "无法启动 Docker Desktop。"

  docker_ready=0
  for attempt in {1..60}; do
    if docker_is_ready; then
      docker_ready=1
      break
    fi
    sleep 1
  done
  [[ "$docker_ready" == 1 ]] || fail "等待 Docker Desktop 启动超时。"
fi

if [[ ! -f .env ]]; then
  [[ -f .env.example ]] || fail "缺少 .env.example。"
  cp .env.example .env
  print "已从 .env.example 创建 .env。"
fi

app_port=$(sed -n 's/^APP_PORT=//p' .env | tail -n 1 | tr -d '[:space:]')
app_port=${app_port:-8080}
app_url="http://127.0.0.1:${app_port}"

print "正在构建并启动服务…"
compose_started=0
for attempt in {1..3}; do
  if docker compose -f deploy/docker-compose.yml up --build -d; then
    compose_started=1
    break
  fi
  print -u2 "镜像构建失败，正在重试（$attempt/3）…"
  sleep 3
done
[[ "$compose_started" == 1 ]] || fail "连续三次构建服务失败，请检查网络和 Docker 日志。"

print "正在准备演示数据（已有数据不会重复创建）…"
docker compose -f deploy/docker-compose.yml exec -T backend \
  uv run --no-sync python -m scripts.seed_demo

print "正在检查服务状态…"
service_ready=0
for attempt in {1..30}; do
  if curl --fail --silent --show-error "$app_url/api/v1/health" >/dev/null 2>&1; then
    service_ready=1
    break
  fi
  sleep 2
done
[[ "$service_ready" == 1 ]] || fail "服务健康检查未通过，请运行 docker compose logs 查看日志。"

print "\n启动成功：$app_url"
print "停止服务：docker compose -f deploy/docker-compose.yml down"

if [[ "${START_LOCAL_NO_OPEN:-0}" != 1 ]]; then
  open "$app_url"
fi
