#!/usr/bin/env bash
# Smoke-test a running Compose stack: platform.hello over HTTP, CLI and a worker job,
# with the permission enforced on each and an audit row per call.
set -euo pipefail

COMPOSE=${COMPOSE:-docker compose}
BASE_URL=${BASE_URL:-http://localhost:8000}
exec_app() { $COMPOSE exec -T app "$@"; }

echo "== seeding smoke users"
exec_app python manage.py shell -c "
from django.contrib.auth.models import Group, User
u, _ = User.objects.get_or_create(username='smoke')
u.set_password('smoke-pass'); u.save()
u.groups.add(Group.objects.get_or_create(name='staff')[0])
n, _ = User.objects.get_or_create(username='smoke-norole')
"

echo "== CLI"
exec_app python manage.py action platform.hello --user smoke --name cli | grep -q '"caller": "cli"'
if exec_app python manage.py action platform.hello --user smoke-norole >/dev/null 2>&1; then
  echo "CLI allowed a user with no role" >&2; exit 1
fi

echo "== job (through the worker)"
exec_app python manage.py action platform.hello --user smoke --enqueue --name job | grep -q '"caller": "job"'

echo "== HTTP"
exec_app python manage.py shell -c "
from django.test import Client
from django.contrib.auth.models import User
c = Client()
assert c.get('/api/v1/hello').status_code == 401
c.force_login(User.objects.get(username='smoke'))
r = c.get('/api/v1/hello', {'name': 'http'})
assert r.status_code == 200 and r.json()['caller'] == 'http', r.content
c.force_login(User.objects.get(username='smoke-norole'))
assert c.get('/api/v1/hello').status_code == 403
"
code=$(curl -sS -o /dev/null -w "%{http_code}" "$BASE_URL/api/v1/hello")
[ "$code" = "401" ] || { echo "expected 401 from the published port, got $code" >&2; exit 1; }

echo "== metric registry (R0 exit: register once, read on every surface)"
exec_app python manage.py shell -c "
from django.contrib.auth.models import Group, User
a, _ = User.objects.get_or_create(username='smoke-admin')
a.groups.add(Group.objects.get_or_create(name='admin')[0])
"
exec_app python manage.py action metric.register --user smoke-admin \
  --json '{"display_name": "Smoke deposits", "metric_code": "smoke_deposits", "direction": "higher_is_better", "aggregation": "sum", "unit": "currency", "products": ["scorecards"]}' \
  | grep -q '"metric_code": "smoke_deposits"'
exec_app python manage.py action metric.get --user smoke-admin --metric-code smoke_deposits --enqueue \
  | grep -q '"display_name": "Smoke deposits"'
exec_app python manage.py shell -c "
from django.test import Client
from django.contrib.auth.models import User
c = Client()
c.force_login(User.objects.get(username='smoke-admin'))
r = c.get('/api/v1/actions/metric.get', {'metric_code': 'smoke_deposits'})
assert r.status_code == 200 and r.json()['periods'][0]['unit'] == 'currency', r.content
names = {a['name'] for a in c.get('/api/v1/registry').json()['actions']}
assert {'metric.register', 'visibility.rebuild', 'period.transition'} <= names, names
"

echo "== ingestion (R0 exit: a feed dry-runs, reports rejections, then loads all-or-nothing)"
exec_app python manage.py action feed.register --user smoke-admin \
  --json '{"name": "smoke_monthly", "template": "actual_monthly", "mode": "upload"}' \
  | grep -q '"dry_run_passed": false'
exec_app python manage.py shell -c "
import base64
from django.test import Client
from django.contrib.auth.models import User
from django.utils import timezone
c = Client()
c.force_login(User.objects.get(username='smoke-admin'))
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
