#!/usr/bin/env bash
# scripts/validate_storyboard.sh
set -e                           # 只开 -e，避免静默退出
export LC_ALL=C

echo "== validate_storyboard.sh =="

# ===== 可配参数（如需可通过环境变量覆盖） =====
API="${API:-http://localhost:8000}"
ENV_FILE="${ENV_FILE:-.env}"
STORY="${STORY:-story.txt}"
CHARA="${CHARA:-character.txt}"
SCENE="${SCENE:-scene.txt}"

# ===== 依赖检查 =====
need() { command -v "$1" >/dev/null 2>&1 || { echo "❌ 需要 $1"; exit 1; }; }
need jq; need curl

# ===== 读取 SERVICE_API_KEY（兼容末尾 =）=====
if [[ ! -f "$ENV_FILE" ]]; then echo "❌ 未找到 $ENV_FILE"; exit 1; fi
KEY="$(awk -F= '/^SERVICE_API_KEY=/{print substr($0,index($0,"=")+1)}' "$ENV_FILE" | tr -d '\r' | head -n1)"
if [[ -z "$KEY" ]]; then echo "❌ SERVICE_API_KEY 为空，请在 $ENV_FILE 设置"; exit 1; fi

# ===== 健康检查 =====
echo "▶ 健康检查：${API}/health"
curl -sS -H "x-api-key: ${KEY}" "${API}/health" | sed 's/^/  /' || { echo; echo "❌ /health 失败"; exit 1; }
echo "----------------------------------------"

# ===== 输入文件存在性 =====
[[ -f "$STORY" ]] || { echo "❌ 未找到 $STORY"; exit 1; }
[[ -f "$CHARA" ]] || { echo "❌ 未找到 $CHARA"; exit 1; }
[[ -f "$SCENE" ]] || { echo "❌ 未找到 $SCENE"; exit 1; }

# ===================== Round1 =====================
echo "▶ Round1：/storyboardn/round1 （读取 $STORY）"
BODY_R1="$(jq -n --rawfile story "$STORY" \
  '{story:$story, min_shots:10, max_shots:20, config:{max_output_tokens:3000, temperature:0.4}}')"

T1=$(date +%s)
curl -sS -H "x-api-key: ${KEY}" -H "Content-Type: application/json" \
  -d "$BODY_R1" "${API}/storyboardn/round1" | tee round1.json | jq . | sed 's/^/  /'
T1D=$(( $(date +%s) - T1 ))
echo "✓ Round1 用时 ${T1D}s"
echo "----------------------------------------"

# ===== 定位 Round1 产物 =====
echo "▶ 定位 Round1 产物（downloads 或 dev_outputs）"
# 你的 round1 返回里 downloads 是对象（pictures_url），优先解析
R1_LOC="$(jq -r '.downloads.pictures_url // empty' round1.json)"

resolve_local_from_data() {
  local url="$1"
  # 如果是 /data/storyboard/2025xxxx/xxx.json，把它映射回本地 dev_outputs 路径
  if [[ "$url" =~ ^/data/storyboard/ ]]; then
    echo "dev_outputs/storyboard/${url#/data/storyboard/}"
  else
    echo ""
  fi
}

TMP=""
if [[ -n "$R1_LOC" ]]; then
  if [[ "$R1_LOC" =~ ^https?:// ]]; then
    TMP="$(mktemp)"; echo "  - 下载：$R1_LOC -> $TMP"
    curl -sS "$R1_LOC" > "$TMP"; R1_FILE="$TMP"
  else
    # 可能是 /data/.../xxx.json 或相对路径
    LOCAL_FROM_DATA="$(resolve_local_from_data "$R1_LOC")"
    if [[ -n "$LOCAL_FROM_DATA" && -f "$LOCAL_FROM_DATA" ]]; then
      echo "  - 使用本地映射：$LOCAL_FROM_DATA"
      R1_FILE="$LOCAL_FROM_DATA"
    elif [[ -f "$R1_LOC" ]]; then
      echo "  - 使用本地路径：$R1_LOC"
      R1_FILE="$R1_LOC"
    else
      echo "  - 未找到本地文件，回退到 dev_outputs"
      LATEST_DIR="$(ls -td dev_outputs/storyboard/* 2>/dev/null | head -1 || true)"
      R1_FILE="$(ls -t "$LATEST_DIR"/*_round1_pictures.json 2>/dev/null | head -1 || true)"
    fi
  fi
fi

# downloads 中如果没给出，回退 dev_outputs
if [[ -z "${R1_FILE:-}" || ! -f "$R1_FILE" ]]; then
  LATEST_DIR="$(ls -td dev_outputs/storyboard/* 2>/dev/null | head -1 || true)"
  R1_FILE="$(ls -t "$LATEST_DIR"/*_round1_pictures.json 2>/dev/null | head -1 || true)"
fi

if [[ -z "${R1_FILE:-}" || ! -f "$R1_FILE" ]]; then
  echo "❌ 未找到 Round1 产物文件（*_round1_pictures.json）"
  exit 1
fi

echo "  - Round1 产物文件：$R1_FILE"
echo "  - 预览文件开头："; head -c 160 "$R1_FILE" | sed 's/^/    /'; echo
echo "----------------------------------------"

# ===== 抽取/映射 pictures 数组 =====
echo "▶ 抽取镜头数组供 Round2"
# 先判断顶层是否就是数组
if jq -e 'type=="array"' "$R1_FILE" >/dev/null 2>&1; then
  PICTURES="$(jq -c '.' "$R1_FILE")"
else
  # 再尝试多路径兜底
  PICTURES="$(jq -ce '
    (.pictures // .result.pictures // .data.pictures // .payload.pictures // .shots // .result.shots // .)
    | select(type=="array")' "$R1_FILE" 2>/dev/null || true)"
fi

# 最小字段映射：保证 shot_id / action / promptv 存在
if [[ -n "$PICTURES" ]]; then
  PICTURES="$(jq -c 'map({
    shot_id,
    action:(.action//""),
    promptv:(.visual//.text//.beat//"")
  })' <<<"$PICTURES")"
fi

LEN="$(jq 'length' <<<"$PICTURES" 2>/dev/null || echo 0)"
echo "  - pictures 数量：$LEN"
if [[ "$LEN" -le 0 ]]; then
  echo "❌ pictures 为空。请运行："
  echo "   jq -C 'keys' \"$R1_FILE\" && jq -C '.[0]' \"$R1_FILE\" 查看结构"
  [[ -n "$TMP" ]] && rm -f "$TMP" || true
  exit 1
fi
echo "----------------------------------------"

# ===================== Round2 =====================
echo "▶ Round2：/storyboardn/round2/batched （读取 $CHARA / $SCENE）"
BODY_R2="$(jq -n --argjson pictures "$PICTURES" \
  --rawfile characters "$CHARA" \
  --rawfile scenes "$SCENE" \
  '{pictures:$pictures, characters:$characters, scenes:$scenes,
    config:{max_output_tokens:3000, temperature:0.4}}')"

T2=$(date +%s)
curl -sS -H "x-api-key: ${KEY}" -H "Content-Type: application/json" \
  -d "$BODY_R2" "${API}/storyboardn/round2/batched" | tee round2.json | jq . | sed 's/^/  /'
T2D=$(( $(date +%s) - T2 ))
echo "✓ Round2 用时 ${T2D}s"
echo "----------------------------------------"

# ===================== Full =====================
echo "▶ Full：/storyboardn/full"
BODY_FULL="$(jq -n --rawfile story "$STORY" --rawfile characters "$CHARA" --rawfile scenes "$SCENE" \
  '{story:$story, characters:$characters, scenes:$scenes, config:{max_output_tokens:3200, temperature:0.4}}')"

T3=$(date +%s)
curl -sS -H "x-api-key: ${KEY}" -H "Content-Type: application/json" \
  -d "$BODY_FULL" "${API}/storyboardn/full" | tee full.json | jq . | sed 's/^/  /'
T3D=$(( $(date +%s) - T3 ))

echo "========================================"
printf "Round1 用时: %ss\nRound2 用时: %ss\nFull   用时: %ss\n" "$T1D" "$T2D" "$T3D"
echo "产物目录： http://localhost:8000/data/storyboard/$(date +%Y%m%d)/"
[[ -n "${TMP:-}" ]] && rm -f "$TMP" || true
