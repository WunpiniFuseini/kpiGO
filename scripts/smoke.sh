#!/usr/bin/env bash
# Smoke-test a running Compose stack: platform.hello over HTTP, CLI and a worker job,
# with the permission enforced on each and an audit row per call.
set -euo pipefail

COMPOSE=${COMPOSE:-docker compose}
BASE_URL=${BASE_URL:-http://localhost:8000}
# Must match the stack's KPIGO_SETUP_TOKEN (CI writes both).
KPIGO_SETUP_TOKEN=${KPIGO_SETUP_TOKEN:?set KPIGO_SETUP_TOKEN to the setup token the stack runs with}
exec_app() { $COMPOSE exec -T app "$@"; }

ADMIN=smoke-admin@bank.example
STAFF=smoke@bank.example
NOROLE=smoke-norole@bank.example
PASS="smoke pass phrase 2026"

echo "== first run: bootstrap the first Admin, who provisions the others (R0 exit: unaided)"
exec_app python manage.py action setup.status | grep -q '"install_fingerprint"'
exec_app python manage.py action setup.bootstrap --json "{\"setup_token\": \"$KPIGO_SETUP_TOKEN\", \"email\": \"$ADMIN\", \"display_name\": \"Smoke Admin\", \"password\": \"$PASS\"}" \
  | grep -q '"admin"'
invite() {
  exec_app python manage.py action user.invite --user "$ADMIN" \
    --json "{\"email\": \"$1\", \"display_name\": \"$1\", \"role_codes\": $2, \"auth_provider\": \"local\"}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["invite_token"])'
}
accept() {
  code=$(curl -sS -o /dev/null -w "%{http_code}" -H 'Content-Type: application/json' \
    -d "{\"token\": \"$1\", \"password\": \"$PASS\"}" "$BASE_URL/api/v1/auth/invite/accept")
  [ "$code" = "200" ] || { echo "invite accept returned $code" >&2; exit 1; }
}
accept "$(invite "$STAFF" '["staff"]')"
accept "$(invite "$NOROLE" '[]')"

echo "== CLI"
exec_app python manage.py action platform.hello --user "$STAFF" --name cli | grep -q '"caller": "cli"'
if exec_app python manage.py action platform.hello --user "$NOROLE" >/dev/null 2>&1; then
  echo "CLI allowed a user with no role" >&2; exit 1
fi

echo "== job (through the worker)"
exec_app python manage.py action platform.hello --user "$STAFF" --enqueue --name job | grep -q '"caller": "job"'

echo "== HTTP: sign in with a password over the published port, then a session"
jar=$(mktemp)
code=$(curl -sS -o /dev/null -w "%{http_code}" "$BASE_URL/api/v1/hello")
[ "$code" = "401" ] || { echo "expected 401 from the published port, got $code" >&2; exit 1; }
code=$(curl -sS -c "$jar" -o /dev/null -w "%{http_code}" -H 'Content-Type: application/json' \
  -d "{\"email\": \"$STAFF\", \"password\": \"$PASS\"}" "$BASE_URL/api/v1/auth/login")
[ "$code" = "200" ] || { echo "login returned $code" >&2; exit 1; }
curl -sS -b "$jar" "$BASE_URL/api/v1/hello?name=http" | grep -q '"caller": "http"'
curl -sS -b "$jar" "$BASE_URL/api/v1/auth/me" | grep -q '"page_key": "scorecards"'
code=$(curl -sS -o /dev/null -w "%{http_code}" -H 'Content-Type: application/json' \
  -d "{\"email\": \"$STAFF\", \"password\": \"wrong\"}" "$BASE_URL/api/v1/auth/login")
[ "$code" = "401" ] || { echo "a wrong password returned $code" >&2; exit 1; }
norole=$(mktemp)
curl -sS -c "$norole" -o /dev/null -H 'Content-Type: application/json' \
  -d "{\"email\": \"$NOROLE\", \"password\": \"$PASS\"}" "$BASE_URL/api/v1/auth/login"
code=$(curl -sS -b "$norole" -o /dev/null -w "%{http_code}" "$BASE_URL/api/v1/hello")
[ "$code" = "403" ] || { echo "a user with no role got $code from hello" >&2; exit 1; }
rm -f "$jar" "$norole"

echo "== health page (R0 exit: reports service, database and feed status)"
exec_app python manage.py action system.health --user "$ADMIN" | python3 -c '
import json, sys
h = json.load(sys.stdin)
names = {s["name"]: s["status"] for s in h["services"]}
assert names["database"] == "ok" and names["queue"] == "ok" and names["workers"] == "ok", names
assert h["feeds"] is not None and h["licence"]["state"], h
print("health ok:", names)
'

echo "== metric registry (R0 exit: register once, read on every surface)"
exec_app python manage.py action metric.register --user "$ADMIN" \
  --json '{"display_name": "Smoke deposits", "metric_code": "smoke_deposits", "direction": "higher_is_better", "aggregation": "sum", "unit": "currency", "products": ["scorecards"]}' \
  | grep -q '"metric_code": "smoke_deposits"'
exec_app python manage.py action metric.get --user "$ADMIN" --metric-code smoke_deposits --enqueue \
  | grep -q '"display_name": "Smoke deposits"'
exec_app python manage.py shell -c "
from django.test import Client
from django.contrib.auth.models import User
c = Client()
c.force_login(User.objects.get(username='smoke-admin@bank.example'))
r = c.get('/api/v1/actions/metric.get', {'metric_code': 'smoke_deposits'})
assert r.status_code == 200 and r.json()['periods'][0]['unit'] == 'currency', r.content
names = {a['name'] for a in c.get('/api/v1/registry').json()['actions']}
assert {'metric.register', 'visibility.rebuild', 'period.transition'} <= names, names
"

echo "== ingestion (R0 exit: a feed dry-runs, reports rejections, then loads all-or-nothing)"
exec_app python manage.py action feed.register --user "$ADMIN" \
  --json '{"name": "smoke_monthly", "template": "actual_monthly", "mode": "upload"}' \
  | grep -q '"dry_run_passed": false'
exec_app python manage.py shell -c "
import base64
from django.test import Client
from django.contrib.auth.models import User
from django.utils import timezone
c = Client()
c.force_login(User.objects.get(username='smoke-admin@bank.example'))
post = lambda name, body: c.post('/api/v1/actions/' + name, body, content_type='application/json')
r = post('subject.register', {'staff_no': 'SMK1', 'full_name': 'Smoke One', 'email': 'smk1@bank.example'})
assert r.status_code == 200, r.content
today = timezone.localdate()
r = post('assignment.create', {'subject_id': r.json()['subject_id'], 'role_code': 'rm',
         'profile_code': 'smoke', 'effective_from': today.isoformat()})
assert r.status_code == 200, r.content
period = f'{today.year:04d}{today.month:02d}'
def upload(rows):
    text = 'metric_code,subject_ref,period_key,actual_value\\n' + ''.join(f'{r}\\n' for r in rows)
    return {'filename': 'smoke.csv', 'content_base64': base64.b64encode(text.encode()).decode()}
bad = post('feed.dry_run', {'feed': 'smoke_monthly', 'upload': upload([f'smoke_deposits,SMK1,{period},10', f'smoke_deposits,NOPE,{period},5'])}).json()
assert bad['passed'] is False and bad['rules'] == {'unknown_subject': 1}, bad
report = c.get('/api/v1/actions/feed.rejections.export', {'run_id': bad['run_id']}).json()
assert 'unknown_subject' in report['content'], report
assert post('feed.run', {'feed': 'smoke_monthly', 'upload': upload([f'smoke_deposits,SMK1,{period},10'])}).status_code == 409
good = post('feed.dry_run', {'feed': 'smoke_monthly', 'upload': upload([f'smoke_deposits,SMK1,{period},10'])}).json()
assert good['passed'] is True, good
loaded = post('feed.run', {'feed': 'smoke_monthly', 'upload': upload([f'smoke_deposits,SMK1,{period},10'])}).json()
assert loaded['outcome'] == 'success' and loaded['rows_accepted'] == 1, loaded
again = post('feed.run', {'feed': 'smoke_monthly', 'upload': upload([f'smoke_deposits,SMK1,{period},10'])}).json()
assert again['idempotent'] is True and again['run_id'] == loaded['run_id'], again
print('ingestion ok')
"

echo "== audit"
exec_app python manage.py shell -c "
from kpigo.platform.models import AuditLog
callers = set(AuditLog.objects.filter(action_name='platform.hello', event='platform.hello.invoked').values_list('caller', flat=True))
assert {'cli', 'job', 'http'} <= callers, callers
assert AuditLog.objects.filter(event='action.denied').count() >= 2
print('audit ok:', sorted(callers))
"
echo "smoke ok"
