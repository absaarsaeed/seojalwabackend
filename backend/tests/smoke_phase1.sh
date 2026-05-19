#!/usr/bin/env bash
# Phase-1 smoke test for SEO Jalwa backend
set -uo pipefail
API_URL=${API_URL:-$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2)}
TS=$(date +%s)
EMAIL="phase1_${TS}@seojalwa.com"
PASS="Testing12345!"
SITE_URL="https://phase1test.com/"

pass=0
fail=0
report=""

check() {
  local name="$1" status="$2" notes="$3"
  if [[ "$status" == "OK" ]]; then
    pass=$((pass+1))
    report+="| $name | OK | $notes |\n"
  else
    fail=$((fail+1))
    report+="| $name | FAIL | $notes |\n"
    echo "FAIL: $name -> $notes"
  fi
}

py() { python3 -c "$1"; }

# ─── 1. Register ───
REG=$(curl -s -X POST "$API_URL/api/auth/register" -H 'Content-Type: application/json' \
  -d "{\"fullName\":\"P1\",\"email\":\"$EMAIL\",\"password\":\"$PASS\",\"websiteUrl\":\"$SITE_URL\"}")
TOKEN=$(echo "$REG" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('data',{}).get('accessToken',''))")
REFRESH=$(echo "$REG" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('data',{}).get('refreshToken',''))")
SITE_ID=$(echo "$REG" | python3 -c "import sys,json;d=json.load(sys.stdin);s=d.get('data',{}).get('sites',[]);print(s[0]['id'] if s else '')")
SITE_KEY=$(echo "$REG" | python3 -c "import sys,json;d=json.load(sys.stdin);s=d.get('data',{}).get('sites',[]);print(s[0]['apiKey'] if s else '')")
if [[ -n "$TOKEN" && -n "$SITE_ID" && "$SITE_KEY" == jalwa_live_* ]]; then
  check "POST /api/auth/register" OK "user+site created, apiKey=$SITE_KEY"
else
  check "POST /api/auth/register" FAIL "$REG"
fi

# ─── 2. Login ───
LOG=$(curl -s -X POST "$API_URL/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}")
[[ $(echo "$LOG" | python3 -c "import sys,json;print(json.load(sys.stdin).get('success'))") == "True" ]] && \
  check "POST /api/auth/login" OK "tokens returned" || check "POST /api/auth/login" FAIL "$LOG"

# ─── 3. /me ───
ME=$(curl -s "$API_URL/api/auth/me" -H "Authorization: Bearer $TOKEN")
SITES_LEN=$(echo "$ME" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d['data']['sites']))")
HAS_USER=$(echo "$ME" | python3 -c "import sys,json;d=json.load(sys.stdin);print('user' in d['data'])")
if [[ "$SITES_LEN" -ge 1 && "$HAS_USER" == "True" ]]; then
  check "GET /api/auth/me" OK "user + subscription + sites ($SITES_LEN)"
else
  check "GET /api/auth/me" FAIL "$ME"
fi

# ─── 4. Forgot password (no email exists) ───
FP=$(curl -s -X POST "$API_URL/api/auth/forgot-password" -H 'Content-Type: application/json' \
  -d "{\"email\":\"nonexistent_${TS}@seojalwa.com\"}")
[[ $(echo "$FP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('success'))") == "True" ]] && \
  check "POST /api/auth/forgot-password" OK "returns success regardless" || \
  check "POST /api/auth/forgot-password" FAIL "$FP"

# ─── 5. Refresh ───
RE=$(curl -s -X POST "$API_URL/api/auth/refresh" -H 'Content-Type: application/json' \
  -d "{\"refreshToken\":\"$REFRESH\"}")
[[ -n "$(echo $RE | python3 -c 'import sys,json;print(json.load(sys.stdin).get("data",{}).get("accessToken",""))')" ]] && \
  check "POST /api/auth/refresh" OK "new accessToken" || check "POST /api/auth/refresh" FAIL "$RE"

# ─── 6. GET /sites ───
SL=$(curl -s "$API_URL/api/sites" -H "Authorization: Bearer $TOKEN")
HAS_KEY=$(echo "$SL" | python3 -c "import sys,json;d=json.load(sys.stdin);print(any('apiKey' in s for s in d['data']))")
[[ "$HAS_KEY" == "True" ]] && check "GET /api/sites" OK "apiKey present" || check "GET /api/sites" FAIL "$SL"

# ─── 7. POST /sites ───
NS=$(curl -s -X POST "$API_URL/api/sites" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Extra","url":"https://extra-site.com","platform":"WORDPRESS"}')
NS_KEY=$(echo "$NS" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('data',{}).get('apiKey',''))")
[[ "$NS_KEY" == jalwa_live_* ]] && check "POST /api/sites" OK "$NS_KEY" || check "POST /api/sites" FAIL "$NS"

# ─── 8. GET /sites/{id} ───
S1=$(curl -s "$API_URL/api/sites/$SITE_ID" -H "Authorization: Bearer $TOKEN")
S1_KEY=$(echo "$S1" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('data',{}).get('apiKey',''))")
[[ "$S1_KEY" == jalwa_live_* ]] && check "GET /api/sites/{id}" OK "apiKey present" || check "GET /api/sites/{id}" FAIL "$S1"

# ─── 9. POST /sites/{id}/verify-connection (will be false; plugin not installed) ───
VC=$(curl -s -X POST "$API_URL/api/sites/$SITE_ID/verify-connection" -H "Authorization: Bearer $TOKEN")
[[ $(echo "$VC" | python3 -c "import sys,json;print(json.load(sys.stdin).get('success'))") == "True" ]] && \
  check "POST /api/sites/{id}/verify-connection" OK "$(echo $VC | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"data\"][\"message\"])')" || \
  check "POST /api/sites/{id}/verify-connection" FAIL "$VC"

# ─── 10. GET /articles ───
AL=$(curl -s "$API_URL/api/articles?siteId=$SITE_ID" -H "Authorization: Bearer $TOKEN")
[[ $(echo "$AL" | python3 -c "import sys,json;print(json.load(sys.stdin).get('success'))") == "True" ]] && \
  check "GET /api/articles" OK "list returned" || check "GET /api/articles" FAIL "$AL"

# ─── 11. POST /articles/generate ───
GEN=$(curl -s -X POST "$API_URL/api/articles/generate" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"siteId\":\"$SITE_ID\",\"searchTerm\":\"phase 1 smoke test\"}")
JOB_ID=$(echo "$GEN" | python3 -c "import sys,json;print(json.load(sys.stdin).get('data',{}).get('jobId',''))")
[[ -n "$JOB_ID" ]] && check "POST /api/articles/generate" OK "jobId=$JOB_ID" || check "POST /api/articles/generate" FAIL "$GEN"

# ─── 12. GET /articles/job/{id} ───
sleep 1
JS=$(curl -s "$API_URL/api/articles/job/$JOB_ID" -H "Authorization: Bearer $TOKEN")
JS_STAT=$(echo "$JS" | python3 -c "import sys,json;print(json.load(sys.stdin).get('data',{}).get('status',''))")
[[ -n "$JS_STAT" ]] && check "GET /api/articles/job/{id}" OK "status=$JS_STAT" || check "GET /api/articles/job/{id}" FAIL "$JS"

# ─── 13. Article calendar ───
Y=$(date +%Y); M=$(date +%-m)
CAL=$(curl -s "$API_URL/api/articles/calendar?siteId=$SITE_ID&year=$Y&month=$M" -H "Authorization: Bearer $TOKEN")
[[ $(echo "$CAL" | python3 -c "import sys,json;print(json.load(sys.stdin).get('success'))") == "True" ]] && \
  check "GET /api/articles/calendar" OK "grouped" || check "GET /api/articles/calendar" FAIL "$CAL"

# ─── 14. GET article settings (defaults) ───
AS=$(curl -s "$API_URL/api/article-settings/$SITE_ID" -H "Authorization: Bearer $TOKEN")
AS_LEN=$(echo "$AS" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d.get('data',{})))")
[[ "$AS_LEN" -ge 15 ]] && check "GET /api/article-settings/{siteId}" OK "$AS_LEN fields (defaults)" || \
  check "GET /api/article-settings/{siteId}" FAIL "$AS"

# ─── 15. PUT article settings ───
PAS=$(curl -s -X PUT "$API_URL/api/article-settings/$SITE_ID" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"publishingFrequency":7,"writingLanguage":"English"}')
[[ $(echo "$PAS" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['data'].get('publishingFrequency'))") == "7" ]] && \
  check "PUT /api/article-settings/{siteId}" OK "saved" || check "PUT /api/article-settings/{siteId}" FAIL "$PAS"

# ─── 16. POST /search-terms ───
ST=$(curl -s -X POST "$API_URL/api/search-terms" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"siteId\":\"$SITE_ID\",\"terms\":[\" maternity tips \",\"maternity tips\",\"baby food\"]}")
STC=$(echo "$ST" | python3 -c "import sys,json;print(json.load(sys.stdin).get('data',{}).get('created',0))")
[[ "$STC" == "2" ]] && check "POST /api/search-terms" OK "2 created (dedup ok)" || check "POST /api/search-terms" FAIL "$ST got=$STC"

# ─── 17. GET /search-terms ───
STL=$(curl -s "$API_URL/api/search-terms?siteId=$SITE_ID" -H "Authorization: Bearer $TOKEN")
[[ $(echo "$STL" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d.get('data',[])))") -ge 2 ]] && \
  check "GET /api/search-terms" OK "list ok" || check "GET /api/search-terms" FAIL "$STL"

# ─── 18. AI Visibility latest ───
AVL=$(curl -s "$API_URL/api/ai-visibility/latest?siteId=$SITE_ID" -H "Authorization: Bearer $TOKEN")
[[ $(echo "$AVL" | python3 -c "import sys,json;print(json.load(sys.stdin).get('success'))") == "True" ]] && \
  check "GET /api/ai-visibility/latest" OK "scan or null" || check "GET /api/ai-visibility/latest" FAIL "$AVL"

# ─── 19. Growth Score ───
GS=$(curl -s "$API_URL/api/growth-score?siteId=$SITE_ID" -H "Authorization: Bearer $TOKEN")
[[ $(echo "$GS" | python3 -c "import sys,json;print(json.load(sys.stdin).get('success'))") == "True" ]] && \
  check "GET /api/growth-score" OK "computed" || check "GET /api/growth-score" FAIL "$GS"

# ─── 20. GET /api/plans (public) ───
PL=$(curl -s "$API_URL/api/plans")
PL_LEN=$(echo "$PL" | python3 -c "import sys,json;print(len(json.load(sys.stdin).get('data',[])))")
[[ "$PL_LEN" -ge 1 ]] && check "GET /api/plans (public)" OK "$PL_LEN plans" || check "GET /api/plans" FAIL "$PL"

# ─── 21. Plugin version (public) ───
PV=$(curl -s "$API_URL/api/plugin/version")
PV_V=$(echo "$PV" | python3 -c "import sys,json;print(json.load(sys.stdin).get('data',{}).get('version',''))")
[[ -n "$PV_V" ]] && check "GET /api/plugin/version" OK "v=$PV_V" || check "GET /api/plugin/version" FAIL "$PV"

# ─── 22. Plugin verify (X-Jalwa-API-Key) ───
PVE=$(curl -s -X POST "$API_URL/api/plugin/verify" -H "X-Jalwa-API-Key: $SITE_KEY")
PVE_V=$(echo "$PVE" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('data',{}).get('valid'))")
[[ "$PVE_V" == "True" ]] && check "POST /api/plugin/verify" OK "valid + connected" || check "POST /api/plugin/verify" FAIL "$PVE"

# ─── 23. Plugin pending articles ───
PPA=$(curl -s "$API_URL/api/plugin/articles/pending" -H "X-Jalwa-API-Key: $SITE_KEY")
[[ $(echo "$PPA" | python3 -c "import sys,json;print(json.load(sys.stdin).get('success'))") == "True" ]] && \
  check "GET /api/plugin/articles/pending" OK "list" || check "GET /api/plugin/articles/pending" FAIL "$PPA"

# ─── 24. Admin login ───
AL=$(curl -s -X POST "$API_URL/api/admin/auth/login" -H 'Content-Type: application/json' \
  -d '{"username":"jalwa","password":"jalwaadmin"}')
ATOKEN=$(echo "$AL" | python3 -c "import sys,json;print(json.load(sys.stdin).get('data',{}).get('token',''))")
[[ -n "$ATOKEN" ]] && check "POST /api/admin/auth/login" OK "token" || check "POST /api/admin/auth/login" FAIL "$AL"

# ─── 25. Admin dashboard stats ───
AS_=$(curl -s "$API_URL/api/admin/dashboard/stats" -H "X-Admin-Token: $ATOKEN")
[[ $(echo "$AS_" | python3 -c "import sys,json;print(json.load(sys.stdin).get('success'))") == "True" ]] && \
  check "GET /api/admin/dashboard/stats" OK "metrics" || check "GET /api/admin/dashboard/stats" FAIL "$AS_"

# ─── 26. Admin users list ───
AU=$(curl -s "$API_URL/api/admin/users" -H "X-Admin-Token: $ATOKEN")
[[ $(echo "$AU" | python3 -c "import sys,json;print(json.load(sys.stdin).get('success'))") == "True" ]] && \
  check "GET /api/admin/users" OK "list" || check "GET /api/admin/users" FAIL "$AU"

# ─── 27. Admin api-keys list ───
AAK=$(curl -s "$API_URL/api/admin/api-keys" -H "X-Admin-Token: $ATOKEN")
AAK_N=$(echo "$AAK" | python3 -c "import sys,json;print(len(json.load(sys.stdin).get('data',[])))")
[[ "$AAK_N" -eq 13 ]] && check "GET /api/admin/api-keys" OK "$AAK_N services rich shape" || \
  check "GET /api/admin/api-keys" FAIL "got $AAK_N"

# ─── 28. Admin api-keys PUT (shape 1) ───
P1=$(curl -s -X PUT "$API_URL/api/admin/api-keys/openai" -H "X-Admin-Token: $ATOKEN" -H 'Content-Type: application/json' \
  -d '{"value":"sk-proj-shape1-abcdefghijk1234"}')
[[ $(echo "$P1" | python3 -c "import sys,json;print(json.load(sys.stdin).get('success'))") == "True" ]] && \
  check "PUT /api/admin/api-keys/{key} (Shape 1)" OK "saved" || check "PUT shape1" FAIL "$P1"

# ─── 29. Admin api-keys PUT (shape 2) ───
P2=$(curl -s -X PUT "$API_URL/api/admin/api-keys/openai" -H "X-Admin-Token: $ATOKEN" -H 'Content-Type: application/json' \
  -d '{"fields":{"api_key":"sk-proj-shape2-zzzzzzzzzz5678"}}')
[[ $(echo "$P2" | python3 -c "import sys,json;print(json.load(sys.stdin).get('success'))") == "True" ]] && \
  check "PUT /api/admin/api-keys/{key} (Shape 2)" OK "saved" || check "PUT shape2" FAIL "$P2"

echo ""
echo "═══════════════════════════════════════"
echo "Phase-1 Smoke Test Report"
echo "═══════════════════════════════════════"
echo "| Endpoint | Status | Notes |"
echo "|---|---|---|"
echo -e "$report"
echo "═══════════════════════════════════════"
echo "Pass: $pass / Fail: $fail"
echo "═══════════════════════════════════════"
exit $fail
