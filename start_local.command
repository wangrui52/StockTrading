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

docker_desktop_is_running() {
  local desktop_status
  desktop_status=$(docker desktop status 2>/dev/null) || return 1
  [[ "$desktop_status" == *running* ]]
}

wait_for_docker() {
  local max_attempts=$1
  local attempt

  for (( attempt = 1; attempt <= max_attempts; attempt++ )); do
    if docker_is_ready; then
      return 0
    fi
    sleep 1
  done

  return 1
}

if ! docker_is_ready; then
  if docker_desktop_is_running; then
    print "Docker Desktop 已运行，正在等待 Docker Engine…"

    if ! wait_for_docker 15; then
      print "检测到 Docker Desktop 已运行但引擎无响应，正在重启…"
      if ! docker desktop restart; then
        print -u2 "Docker Desktop 命令重启失败，尝试重新打开应用…"
        osascript -e 'tell application "Docker" to quit' >/dev/null 2>&1 || true
        sleep 3
        open -a Docker || fail "无法重启 Docker Desktop。"
      fi
    fi
  else
    print "Docker Desktop 尚未运行，正在启动…"
    open -a Docker || fail "无法启动 Docker Desktop。"
  fi

  wait_for_docker 90 || fail "Docker Engine 启动超时。请打开 Docker Desktop，在 Troubleshoot 中选择 Restart 后重试。"
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

if [[ "${START_LOCAL_DEMO:-0}" == 1 ]]; then
  print "显式启用演示模式：正在准备固定样本（非真实行情）…"
  docker compose -f deploy/docker-compose.yml exec -T backend \
    uv run --no-sync python -m scripts.seed_demo
fi

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
print "请在行情看板点击“同步最新交易日”（空数据库为“同步数据”）获取真实行情。"
print "停止服务：docker compose -f deploy/docker-compose.yml down"

if [[ "${START_LOCAL_NO_OPEN:-0}" != 1 ]]; then
  open "$app_url"
fi
