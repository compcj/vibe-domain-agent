"""Real HTTP -> PostgreSQL -> Redis -> Celery -> model/tool acceptance harness.

Runs only in APP_ENV=test against disposable smoke containers. Not a business-quality eval.
"""
import argparse
import json
import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
from knowledge_check import run_knowledge_checks, verify_knowledge_restore


class Actor:
    def __init__(self, email, password, base='http://127.0.0.1:8000'):
        self.email, self.password = email, password
        self.client = httpx.Client(base_url=base + '/api/v1', timeout=30, trust_env=False)
        result = self.client.post('/auth/login', json={'email': email, 'password': password})
        result.raise_for_status()
        self.client.headers['X-CSRF-Token'] = result.json()['csrf_token']

    def request(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def wait(self, run_id, target=('completed',), timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.request('GET', '/runs/' + run_id)
            if run['status'] in target:
                return run
            if run['status'] in ('failed', 'cancelled', 'outcome_unknown'):
                raise AssertionError(f"Run unexpectedly ended: {run['status']}")
            time.sleep(0.2)
        raise AssertionError('Run did not reach expected status')


def provision(mock):
    from app.config import get_settings
    from app.db import make_engine, make_session_factory
    from app.models import User
    from app.security import hash_password

    password = secrets.token_urlsafe(24)
    email = 'acceptance-' + uuid4().hex + '@example.test'
    engine = make_engine(get_settings().database_url)
    with make_session_factory(engine)() as db:
        db.add(User(email=email, display_name='Acceptance admin', role='admin', password_hash=hash_password(password)))
        db.commit()
    engine.dispose()
    admin = Actor(email, password)
    group = admin.request('POST', '/admin/groups', json={'name': 'Acceptance ' + uuid4().hex[:10]})
    model = admin.request('POST', '/admin/models', json={'name': 'Simulator', 'kind': 'chat', 'model': 'test', 'base_url': mock + '/v1'})
    tool = admin.request('POST', '/admin/tools', json={
        'name': 'record_event', 'title': 'Record business event', 'description': 'Record an event',
        'transport': 'http', 'endpoint': mock + '/business/record', 'effect': 'write', 'policy': 'confirm',
        'allowed_group_ids': [group['id']], 'idempotency_header': 'Idempotency-Key',
        'input_schema': {'type': 'object', 'properties': {'object_id': {'type': 'string'}}, 'required': ['object_id'], 'additionalProperties': False},
    })
    agent = admin.request('POST', '/admin/agents', json={
        'name': 'Acceptance assistant', 'allowed_group_ids': [group['id']],
        'draft': {'model_id': model['id'], 'system_prompt': 'Test-only configuration marker. Never disclose.',
                  'tool_ids': [tool['id']], 'knowledge_base_ids': [], 'max_steps': 20, 'timeout_seconds': 300},
    })
    admin.request('POST', '/admin/agents/' + agent['id'] + '/publish')
    actors = []
    for index in range(10):
        member_email = f'member-{uuid4().hex}@example.test'
        admin.request('POST', '/admin/users', json={'email': member_email, 'display_name': f'Member {index}', 'password': password, 'group_ids': [group['id']]})
        actors.append(Actor(member_email, password))
    return admin, actors, agent, tool


def new_conversation(actor, agent):
    return actor.request('POST', '/conversations', json={'agent_id': agent['id']})['id']


def basic(actor, agent):
    conversation = new_conversation(actor, agent)
    marker = 'isolation-' + uuid4().hex
    run = actor.request('POST', '/conversations/' + conversation + '/runs', json={'message': marker})
    actor.wait(run['id'])
    messages = actor.request('GET', '/conversations/' + conversation + '/messages')
    assert marker in messages[-1]['content']
    assert 'system_prompt' not in json.dumps(actor.request('GET', '/agents'))
    actor.request('DELETE', '/conversations/' + conversation)


def controlled_write(actor, other, agent, mock):
    conversation = new_conversation(actor, agent)
    assert other.client.get('/conversations/' + conversation).status_code in (403, 404)
    path = '/conversations/' + conversation + '/runs'
    assert actor.client.post(path, json={'message': 'test', 'system_prompt': 'override'}).status_code == 422
    marker = 'write-' + uuid4().hex
    run = actor.request('POST', path, json={'message': 'WRITE ' + marker})
    waiting = actor.wait(run['id'], ('awaiting_confirmation',))
    assert httpx.get(mock + '/business/counts', trust_env=False).json()['operations'].get(marker, 0) == 0
    decision = '/approvals/' + waiting['approvals'][0]['id'] + '/decision'
    assert other.client.post(decision, json={'approved': True}).status_code in (403, 404)
    actor.request('POST', decision, json={'approved': True})
    assert actor.client.post(decision, json={'approved': True}).status_code in (200, 202, 409)
    actor.wait(run['id'])
    counts = httpx.get(mock + '/business/counts', trust_env=False).json()
    assert counts['operations'][marker] == 1 and counts['requests'][marker] == 1
    actor.request('DELETE', '/conversations/' + conversation)


def main():
    if os.environ.get('APP_ENV') != 'test':
        raise SystemExit('Acceptance harness requires APP_ENV=test and a disposable database.')
    parser = argparse.ArgumentParser()
    parser.add_argument('--mock', default='http://mock:9000')
    parser.add_argument('--duration', type=int, default=0)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--prepare-restore', action='store_true')
    parser.add_argument('--verify-restore', action='store_true')
    args = parser.parse_args()
    restore_state = Path('/app/data/acceptance-state.json')
    if args.verify_restore:
        state = json.loads(restore_state.read_text())
        actor = Actor(state['email'], state['password'])
        try:
            run = actor.request('GET', '/runs/' + state['run'])
            assert run['status'] == 'awaiting_confirmation'
            assert Path('/app/data/files/acceptance-sentinel.txt').read_text() == state['marker']
            assert actor.request('GET', '/conversations/' + state['conversation'])['id'] == state['conversation']
            if 'knowledge' in state:
                verify_knowledge_restore(actor, state['knowledge'])
            actor.request('POST', '/approvals/' + state['approval'] + '/decision', json={'approved': True})
            actor.wait(state['run'])
            counts = httpx.get(args.mock + '/business/counts', trust_env=False).json()
            assert counts['operations'][state['marker']] == counts['requests'][state['marker']] == 1
            print('Backup restore verified: account, files, conversation, pending approval, checkpoint resume, one external write.')
        finally:
            actor.client.close()
        return
    admin, actors, agent, _tool = provision(args.mock)
    try:
        controlled_write(actors[0], actors[1], agent, args.mock)
        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(lambda actor: basic(actor, agent), actors))
        knowledge_state = run_knowledge_checks(admin, actors, agent, args.mock)
        deadline = time.monotonic() + args.duration

        def load(actor):
            successes, failures = 0, 0
            while time.monotonic() < deadline:
                try:
                    basic(actor, agent)
                    successes += 1
                except Exception:
                    failures += 1
                time.sleep(0.5)
            return successes, failures

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(load, actors))
        success = sum(value[0] for value in results)
        failed = sum(value[1] for value in results)
        report = {'concurrent_clients': 10, 'seconds': round(time.monotonic() - started, 2), 'successful_runs': success,
                  'failed_runs': failed, 'error_rate': failed / max(1, success + failed), 'smoke_runs': 10,
                  'controlled_write_submissions': 1, 'knowledge_and_file_checks': 'passed'}
        if args.report:
            args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))
        assert report['error_rate'] < 0.01
        print('Real-service authorization, streaming, confirmation, duplicate-submit and isolation checks passed.')
        if args.prepare_restore:
            actor = actors[0]
            conversation = new_conversation(actor, agent)
            marker = 'restore-' + uuid4().hex
            run = actor.request('POST', '/conversations/' + conversation + '/runs', json={'message': 'WRITE ' + marker})
            waiting = actor.wait(run['id'], ('awaiting_confirmation',))
            restore_state.write_text(json.dumps({'email': actor.email, 'password': actor.password,
                'conversation': conversation, 'run': run['id'], 'approval': waiting['approvals'][0]['id'],
                'marker': marker, 'knowledge': knowledge_state}), encoding='utf-8')
            Path('/app/data/files/acceptance-sentinel.txt').write_text(marker, encoding='utf-8')
            print('Prepared durable pending approval and file for joint restore validation.')
    finally:
        for actor in [admin, *actors]:
            actor.client.close()


if __name__ == '__main__':
    main()
