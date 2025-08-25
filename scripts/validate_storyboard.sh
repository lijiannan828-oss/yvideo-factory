cat > scripts/validate_storyboard_safe.sh <<'SH'
#!/usr/bin/env bash
set -e   # 只开 -e；不用 -u/pipefail，避免静默退出
export LC_ALL=C
printf '== validate_storyboard_safe.sh ==\n'

API="${API:-http://localhost:8000}"
ENV_FILE="${ENV_FILE:-.env}"
STORY="${STORY:-story.txt}"
CHARA="${CHARA:-character.txt}"
SCENE="${SCENE:-scene.txt}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "❌ 需要 $1"; exit 1; }; }
need jq; need curl

# 读取 SERVICE_API_KEY（兼容末尾=）
if [[ ! -f "$ENV_FILE" ]]; then echo "❌ 未找到 $ENV_FILE"; exit 1; fi
KEY="$(awk -F= '/^SERVICE_API_KEY=/{print substr($0,index($0,"=")+1)}' "$ENV_FILE" | tr -d '\r' | head -n1)"
if [[ -z "$KEY" ]]; then echo "❌ SERVICE_API_KEY 为空，请先在 $ENV_FILE 设置"; exit 1; fi
echo "▶ 健康检查：${API}/health"
curl -sS -H "x-api-key: ${KEY}" "${API}/health" | sed 's/^/  /' || { echo; echo "❌ /health 失败"; exit 1; }
echo "----------------------------------------"

[[ -f "$STORY" ]] || { echo "❌ 未找到 $STORY"; exit 1; }
[[ -f "$CHARA" ]] || { echo "❌ 未找到 $CHARA"; exit 1; }
[[ -f "$SCENE" ]] || { echo "❌ 未找到 $SCENE"; exit 1; }

# ===== Round1 =====
echo "▶ Round1：/storyboardn/round1"
BODY_R1="$(jq -n --rawfile story "$STORY" \
  '{story:$story, min_shots:10, max_shots:20, config:{max_output_tokens:3000, temperature:0.4}}')"

T1=$(date +%s)
curl -sS -H "x-api-key: ${KEY}" -H "Content-Type: application/json" \
  -d "$BODY_R1" "${API}/storyboardn/round1" | tee round1.json | jq . | sed 's/^/  /'
T1D=$(( $(date +%s) - T1 ))
echo "✓ Round1 用时 ${T1D}s"
echo "----------------------------------------"

# 定位 Round1 产物
echo "▶ 定位 Round1 产物（downloads 或 dev_outputs）"
R1_LOC="$(jq -r '.downloads[]? | select(((.name // .type // .file // "") | test("round1_pictures"))) | .path // .url // empty' round1.json | head -1)"
if [[ -n "$R1_LOC" ]]; then
  if [[ "$R1_LOC" =~ ^https?:// ]]; then
    TMP="$(mktemp)"; echo "  - 下载 $R1_LOC -> $TMP"; curl -sS "$R1_LOC" > "$TMP"; R1_FILE="$TMP"
  else
    echo "  - 本地路径：$R1_LOC"; R1_FILE="$R1_LOC"
  fi
else
  LATEST_DIR="$(ls -td dev_outputs/storyboard/* 2>/dev/null | head -1 || true)"
  R1_FILE="$(ls -t "$LATEST_DIR"/*_round1_pictures.json 2>/dev/null | head -1 || true)"
  [[ -n "$R1_FILE" ]] || { echo "❌ 未找到 *_round1_pictures.json"; exit 1; }
  echo "  - 回退使用本地最新：$R1_FILE"
fi
echo "  - 预览："; head -c 120 "$R1_FILE" | sed 's/^/    /'; echo
echo "----------------------------------------"

# 抽取/映射 pictures 数组
echo "▶ 抽取镜头数组供 Round2"
PICTURES="$(jq -ce '(.pictures // .result.pictures // .data.pictures // .payload.pictures // .shots // .result.shots // .)
                    | select(type=="array")' "$R1_FILE" 2>/dev/null || true)"
if [[ -z "$PICTURES" ]]; then
  echo "  - 直接未找到数组，尝试映射最小字段"
  PICTURES="$(jq -c '(.pictures // .result.pictures // .data.pictures // .payload.pictures // .shots // .result.shots // .)
                    | select(type=="array")
                    | map({shot_id: .shot_id, action:(.action//""), promptv:(.visual//.text//.beat//"")})' "$R1_FILE" 2>/dev/null || true)"
fi
LEN="$(jq 'length' <<<"$PICTURES" 2>/dev/null || echo 0)"
echo "  - pictures 数量：$LEN"
[[ "$LEN" -gt 0 ]] || { echo "❌ pictures 为空，检查 $R1_FILE"; exit 1; }
echo "----------------------------------------"

# ===== Round2 =====
echo "▶ Round2：/storyboardn/round2/batched"
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

# ===== Full =====
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
SH

chmod +x scripts/validate_storyboard_safe.sh
bash scripts/validate_storyboard_safe.sh
