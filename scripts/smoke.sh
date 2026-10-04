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

echo "== audit"
exec_app python manage.py shell -c "
from kpigo.platform.models import AuditLog
callers = set(AuditLog.objects.filter(action_name='platform.hello', event='platform.hello.invoked').values_list('caller', flat=True))
assert {'cli', 'job', 'http'} <= callers, callers
assert AuditLog.objects.filter(event='action.denied').count() >= 2
print('audit ok:', sorted(callers))
"
echo "smoke ok"
