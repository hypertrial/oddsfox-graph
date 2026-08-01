#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
runtime_root="${ODDSFOX_RUNTIME_ROOT:-$repo_root/.oddsfox-runtime}"
bootstrap_python="${PYTHON_BIN:-python}"
venv_root="$runtime_root/venv"
python_bin="${ODDSFOX_PYTHON:-$venv_root/bin/python}"
hf_bin="${HF_BIN:-$venv_root/bin/hf}"
llama_server_bin="${LLAMA_SERVER_BIN:-llama-server}"

qwen_revision="bc640142c66e1fdd12af0bd68f40445458f3869b"
granite_revision="7cdf86ccd1f1bb3491c9b7017b033f2e51367397"
embedding_revision="1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
nli_revision="975123f23a50424f9ca95d5382504d24d9ed9fd2"
qwen_id="Qwen/Qwen3-4B-GGUF:Q8_0"
granite_id="ibm-granite/granite-3.3-2b-instruct-GGUF:Q8_0"

model_root="$runtime_root/models"
cache_root="$runtime_root/cache/v11"
output_root="$runtime_root/output"
manifest_root="$runtime_root/manifests"
log_root="$runtime_root/logs"
pid_root="$runtime_root/run"
tmp_root="$runtime_root/tmp"
playwright_root="$runtime_root/playwright"
npm_cache_root="$runtime_root/npm-cache"
qwen_path="$model_root/qwen/Qwen3-4B-Q8_0.gguf"
granite_path="$model_root/granite/granite-3.3-2b-instruct-Q8_0.gguf"
primary_manifest="$manifest_root/primary.json"
verifier_manifest="$manifest_root/verifier.json"
compute_profile="$repo_root/config/local-compute-profile.json"
catalog="$repo_root/data/polymarket_all_markets_20260730T093857Z.parquet"
qualification_out="$output_root/qualification"
smoke_out="$output_root/smoke"
fast_out="$output_root/fast"
full_out="$output_root/full"

export HF_HOME="$runtime_root/huggingface"
export HF_HUB_CACHE="$HF_HOME/hub"
export HF_XET_CACHE="$HF_HOME/xet"
export SENTENCE_TRANSFORMERS_HOME="$runtime_root/huggingface/hub"
export TORCH_HOME="$runtime_root/torch"
export XDG_CACHE_HOME="$runtime_root/xdg-cache"
export PIP_CACHE_DIR="$runtime_root/pip-cache"
export PLAYWRIGHT_BROWSERS_PATH="$playwright_root"
export npm_config_cache="$npm_cache_root"
export TMPDIR="$tmp_root"
export TOKENIZERS_PARALLELISM=false
export HF_HUB_DISABLE_TELEMETRY=1
export PATH="$venv_root/bin:$PATH"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

ensure_runtime_root() {
  mkdir -p "$runtime_root"
  local physical_runtime_root
  physical_runtime_root="$(CDPATH='' cd -P -- "$runtime_root" && pwd)"
  case "$physical_runtime_root/" in
    /Volumes/Mac\ SSD/*) ;;
    *)
      if [[ "${ODDSFOX_ALLOW_NON_SSD_RUNTIME:-0}" != "1" ]]; then
        die "runtime root must stay on /Volumes/Mac SSD (got $physical_runtime_root)"
      fi
      ;;
  esac
  mkdir -p \
    "$model_root/qwen" "$model_root/granite" "$cache_root" "$output_root" \
    "$manifest_root" "$log_root" "$pid_root" "$tmp_root" "$HF_HUB_CACHE" \
    "$HF_XET_CACHE" "$SENTENCE_TRANSFORMERS_HOME" "$TORCH_HOME" \
    "$XDG_CACHE_HOME" "$PIP_CACHE_DIR" "$PLAYWRIGHT_BROWSERS_PATH" \
    "$npm_config_cache"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"
}

require_file() {
  [[ -f "$1" ]] || die "required file is missing: $1"
}

run_cli() {
  [[ -x "$python_bin" ]] || die "Python environment is missing; run scripts/local_stack.sh install"
  (cd "$repo_root" && "$python_bin" -m oddsfox_graph.cli "$@")
}

run_cli_awake() {
  require_command caffeinate
  [[ -x "$python_bin" ]] || die "Python environment is missing; run scripts/local_stack.sh install"
  (cd "$repo_root" && caffeinate -dimsu "$python_bin" -m oddsfox_graph.cli "$@")
}

download_assets() {
  require_command "$hf_bin"
  "$hf_bin" download Qwen/Qwen3-4B-GGUF Qwen3-4B-Q8_0.gguf \
    --revision "$qwen_revision" --local-dir "$model_root/qwen"
  "$hf_bin" download ibm-granite/granite-3.3-2b-instruct-GGUF \
    granite-3.3-2b-instruct-Q8_0.gguf \
    --revision "$granite_revision" --local-dir "$model_root/granite"
  "$hf_bin" download sentence-transformers/all-MiniLM-L6-v2 \
    --revision "$embedding_revision"
  "$hf_bin" download tasksource/ModernBERT-base-nli \
    --revision "$nli_revision"
}

install_dependencies() {
  require_command brew
  require_command "$bootstrap_python"
  if ! brew list llama.cpp >/dev/null 2>&1; then
    brew install llama.cpp
  fi
  if [[ ! -x "$python_bin" ]]; then
    "$bootstrap_python" -m venv "$venv_root"
  fi
  "$python_bin" -m pip install -c "$repo_root/constraints-dev.txt" \
    -e "${repo_root}[dev]"
}

model_is_ready() {
  local port="$1"
  local model_id="$2"
  curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1 && \
    curl -fsS "http://127.0.0.1:$port/v1/models" | \
      grep -Fq "\"$model_id\""
}

managed_model_is_running() {
  local pid_file="$1"
  local port="$2"
  local model_id="$3"
  [[ -f "$pid_file" ]] || return 1
  local pid command_line
  pid="$(tr -d '[:space:]' < "$pid_file")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" >/dev/null 2>&1 || return 1
  command_line="$(ps -p "$pid" -o command= 2>/dev/null)" || return 1
  [[ "$command_line" == *"$llama_server_bin"* ]] || return 1
  [[ "$command_line" == *"--port $port"* ]] || return 1
  [[ "$command_line" == *"--alias $model_id"* ]]
}

wait_for_model() {
  local port="$1"
  local model_id="$2"
  local _attempt
  for _attempt in $(seq 1 300); do
    if model_is_ready "$port" "$model_id"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

start_model() {
  local role="$1"
  local model_path="$2"
  local model_id="$3"
  local port="$4"
  local pid_file="$pid_root/$role.pid"
  local log_file="$log_root/$role.log"
  require_file "$model_path"
  if managed_model_is_running "$pid_file" "$port" "$model_id"; then
    printf '%s is already running (pid %s)\n' "$role" "$(cat "$pid_file")"
    return
  fi
  if model_is_ready "$port" "$model_id"; then
    printf '%s is already available at http://127.0.0.1:%s/v1\n' "$role" "$port"
    return
  fi
  if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
    die "port $port is already serving another process"
  fi
  nohup "$llama_server_bin" \
    --model "$model_path" \
    --alias "$model_id" \
    --host 127.0.0.1 \
    --port "$port" \
    --ctx-size 16384 \
    --n-gpu-layers 99 \
    --parallel 2 \
    --jinja \
    --cors-origins localhost \
    --no-cors-credentials \
    --no-webui \
    >"$log_file" 2>&1 &
  printf '%s\n' "$!" > "$pid_file"
  if ! wait_for_model "$port" "$model_id"; then
    tail -80 "$log_file" >&2 || true
    if managed_model_is_running "$pid_file" "$port" "$model_id"; then
      kill "$(tr -d '[:space:]' < "$pid_file")" || true
    fi
    : > "$pid_file"
    die "model $model_id did not become ready on port $port"
  fi
  printf '%s ready at http://127.0.0.1:%s/v1\n' "$role" "$port"
}

start_models() {
  require_command "$llama_server_bin"
  start_model primary "$qwen_path" "$qwen_id" 8080
  start_model verifier "$granite_path" "$granite_id" 8081
}

stop_model() {
  local role="$1"
  local port="$2"
  local model_id="$3"
  local pid_file="$pid_root/$role.pid"
  if ! managed_model_is_running "$pid_file" "$port" "$model_id"; then
    : > "$pid_file"
    printf '%s is not running\n' "$role"
    return
  fi
  local pid
  pid="$(tr -d '[:space:]' < "$pid_file")"
  kill "$pid"
  for _ in $(seq 1 30); do
    if ! managed_model_is_running "$pid_file" "$port" "$model_id"; then
      : > "$pid_file"
      printf '%s stopped\n' "$role"
      return
    fi
    sleep 1
  done
  die "$role process $pid did not stop cleanly"
}

create_manifests() {
  require_file "$qwen_path"
  require_file "$granite_path"
  run_cli model-manifest \
    --model-path "$qwen_path" \
    --model-id "$qwen_id" \
    --revision "$qwen_revision" \
    --license Apache-2.0 \
    --runtime llama.cpp \
    --llm-base-url http://127.0.0.1:8080/v1 \
    --output "$primary_manifest"
  run_cli model-manifest \
    --model-path "$granite_path" \
    --model-id "$granite_id" \
    --revision "$granite_revision" \
    --license Apache-2.0 \
    --runtime llama.cpp \
    --llm-base-url http://127.0.0.1:8081/v1 \
    --output "$verifier_manifest"
}

check_stack() {
  require_file "$primary_manifest"
  require_file "$verifier_manifest"
  run_cli doctor \
    --mode full --input "$catalog" --out "$full_out" --cache-dir "$cache_root" \
    --automation-profile "$qualification_out/automation_profile.json" \
    --primary-model-manifest "$primary_manifest" \
    --verifier-model-manifest "$verifier_manifest" \
    --primary-base-url http://127.0.0.1:8080/v1 \
    --verifier-base-url http://127.0.0.1:8081/v1 \
    --compute-profile "$compute_profile" --output-format json
}

common_discovery_args() {
  printf '%s\0' \
    --input "$catalog" \
    --cache-dir "$cache_root" \
    --primary-model-manifest "$primary_manifest" \
    --verifier-model-manifest "$verifier_manifest" \
    --primary-base-url http://127.0.0.1:8080/v1 \
    --verifier-base-url http://127.0.0.1:8081/v1 \
    --compute-profile "$compute_profile" \
    --max-candidates 400000 \
    --max-llm-pairs 5000 \
    --llm-concurrency 2
}

read_common_args() {
  common_args=()
  while IFS= read -r -d '' value; do
    common_args+=("$value")
  done < <(common_discovery_args)
}

run_qualification() {
  read_common_args
  run_cli_awake qualify "${common_args[@]}" --out "$qualification_out" \
    --seed 0 --output-format json
}

run_smoke() {
  read_common_args
  run_cli_awake discover --mode full "${common_args[@]}" --out "$smoke_out" \
    --automation-profile "$qualification_out/automation_profile.json" \
    --max-propositions 5000 --progress-format plain --output-format json
}

run_fast() {
  run_cli discover --mode fast --input "$catalog" --out "$fast_out" \
    --deadline-seconds 120 --progress-format plain --output-format json
}

run_full() {
  require_file "$qualification_out/automation_profile.json"
  start_models
  read_common_args
  run_cli_awake discover --mode full "${common_args[@]}" --out "$full_out" \
    --automation-profile "$qualification_out/automation_profile.json" \
    --deadline-seconds 3600 --progress-format plain --output-format json
}

run_web_checks() {
  require_command npm
  require_command npx
  (
    cd "$repo_root/web"
    npm ci
    npm run lint
    npm run typecheck
    npm run test
    npm run build
    npm run check-generated-assets
    npx playwright install chromium
    npm run test:e2e
  )
  git -C "$repo_root" diff --exit-code -- oddsfox_graph/static/explorer
}

show_status() {
  local role port pid_file model_id
  for role in primary verifier; do
    if [[ "$role" == "primary" ]]; then
      port=8080
      model_id="$qwen_id"
    else
      port=8081
      model_id="$granite_id"
    fi
    pid_file="$pid_root/$role.pid"
    if managed_model_is_running "$pid_file" "$port" "$model_id"; then
      printf '%s: running pid=%s endpoint=http://127.0.0.1:%s/v1\n' \
        "$role" "$(cat "$pid_file")" "$port"
      curl -fsS "http://127.0.0.1:$port/v1/models"
      printf '\n'
    elif model_is_ready "$port" "$model_id"; then
      printf '%s: running externally endpoint=http://127.0.0.1:%s/v1\n' \
        "$role" "$port"
      curl -fsS "http://127.0.0.1:$port/v1/models"
      printf '\n'
    else
      printf '%s: stopped\n' "$role"
    fi
  done
}

show_paths() {
  printf 'repository: %s\n' "$repo_root"
  printf 'runtime:    %s\n' "$runtime_root"
  printf 'Python:     %s\n' "$python_bin"
  printf 'pip cache:  %s\n' "$PIP_CACHE_DIR"
  printf 'npm cache:  %s\n' "$npm_config_cache"
  printf 'browsers:   %s\n' "$PLAYWRIGHT_BROWSERS_PATH"
  printf 'models:     %s\n' "$model_root"
  printf 'HF cache:   %s\n' "$HF_HOME"
  printf 'cache:      %s\n' "$cache_root"
  printf 'outputs:    %s\n' "$output_root"
  printf 'fast graph: %s\n' "$fast_out"
  printf 'full graph: %s\n' "$full_out"
  printf 'viewer:     http://127.0.0.1:8765\n'
}

usage() {
  cat <<'EOF'
Usage: scripts/local_stack.sh COMMAND

Commands:
  paths       Show all SSD-resident runtime paths.
  install     Install llama.cpp and the SSD-resident Python environment.
  download    Download pinned Qwen, Granite, MiniLM, and ModernBERT assets.
  setup       Run install and download.
  start       Start Qwen on 8080 and Granite on 8081.
  stop        Stop model processes started by this script.
  status      Show process and loaded-model status.
  manifests   Create runtime-bound primary and verifier manifests.
  check       Run doctor, including both model conformance checks.
  qualify     Run automated catalog-derived qualification.
  fast        Build the complete deterministic catalog graph without models.
  full        Upgrade to the experimental ANN/NLI/dual-model graph.
  serve-fast  Serve the completed fast graph.
  serve-full  Serve the completed full graph.
  smoke       Run a 5,000-proposition discovery build.
  web-check   Build and test the explorer using SSD-resident browser/npm caches.
  summary     Print the completed fast all-market run summary.
EOF
}

ensure_runtime_root
case "${1:-}" in
  paths) show_paths ;;
  install) install_dependencies ;;
  download) download_assets ;;
  setup) install_dependencies; download_assets ;;
  start) start_models ;;
  stop) stop_model primary 8080 "$qwen_id"; stop_model verifier 8081 "$granite_id" ;;
  status) show_status ;;
  manifests) create_manifests ;;
  check) check_stack ;;
  qualify) run_qualification ;;
  smoke) run_smoke ;;
  fast) run_fast ;;
  full) run_full ;;
  web-check) run_web_checks ;;
  summary) run_cli run-summary --out "$fast_out" --output-format json ;;
  serve-fast) run_cli serve --out "$fast_out" --host 127.0.0.1 --port 8765 --open-browser ;;
  serve-full) run_cli serve --out "$full_out" --host 127.0.0.1 --port 8765 --open-browser ;;
  *) usage; exit 2 ;;
esac
