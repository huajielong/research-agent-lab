import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

test_dir = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = 'sqlite:///' + test_dir.name.replace('\\', '/') + '/tasks.db'
os.environ.pop('RESET_DB_ON_STARTUP', None)
os.environ['APP_API_KEY'] = 'test-workspace-secret'
os.environ['ALLOW_ANONYMOUS'] = '0'
agents = types.ModuleType('src.planning_agent')
agents.planner_agent = lambda prompt: ['Research agent: Search', 'Writer agent: Write']
agents.executor_agent_step = lambda *args: (args[0], 'writer_agent', '# Report\nA finding [1].')
sys.modules['src.planning_agent'] = agents

from fastapi.testclient import TestClient
import main


class ApiTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        main.engine.dispose()
        test_dir.cleanup()

    def setUp(self):
        self.client = TestClient(main.app, headers={'X-API-Key': 'test-workspace-secret'})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        main.task_progress.clear()
        from src.security import rate_events
        rate_events.clear()
        with main.SessionLocal() as db:
            db.query(main.Task).delete()
            db.commit()

    def test_blank_prompt_rejected(self):
        self.assertEqual(self.client.post('/generate_report', json={'prompt': '   '}).status_code, 422)

    def test_history_and_persisted_progress(self):
        with patch.object(main.threading, 'Thread'):
            response = self.client.post('/generate_report', json={'prompt': 'Test research'})
        task_id = response.json()['task_id']
        main.plan_and_run(task_id, 'Test research')
        tasks = self.client.get('/tasks').json()['tasks']
        self.assertEqual(tasks[0]['id'], task_id)
        self.assertEqual(tasks[0]['status'], 'done')
        main.task_progress.clear()
        progress = self.client.get('/task_progress/' + task_id).json()
        self.assertEqual(len(progress['steps']), 2)
        self.assertEqual(progress['steps'][-1]['status'], 'done')
        self.assertIn('Report', self.client.get('/task_status/' + task_id).json()['result']['html_report'])

    def test_planning_failure_persisted(self):
        with patch.object(main.threading, 'Thread'):
            task_id = self.client.post('/generate_report', json={'prompt': 'Fail'}).json()['task_id']
        with patch.object(main, 'planner_agent', side_effect=RuntimeError('Provider unavailable')):
            main.plan_and_run(task_id, 'Fail')
        task = self.client.get('/task_status/' + task_id).json()
        self.assertEqual(task['status'], 'error')
        self.assertNotIn('Provider unavailable', str(task))
        self.assertIn('暂时不可用', task['result']['error'])

    def test_execution_errors_are_generic_in_storage_progress_and_status(self):
        secret = 'provider endpoint=https://internal.invalid request=private-test-token'
        for failing_output in (False, True):
            with self.subTest(failing_output=failing_output), patch.object(main.threading, 'Thread'):
                task_id = self.client.post('/generate_report', json={'prompt': 'Failure test'}).json()['task_id']
            behavior = {'return_value': ('description', 'research', '[模型错误：' + secret + ']')} if failing_output else {'side_effect': RuntimeError(secret)}
            with patch.object(main, 'executor_agent_step', **behavior):
                main.plan_and_run(task_id, 'Failure test')
            for path in ('/task_status/' + task_id, '/task_progress/' + task_id):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('private-test-token', response.text)
                self.assertNotIn('internal.invalid', response.text)
                self.assertIn('暂时不可用', response.text)
            with main.SessionLocal() as db:
                self.assertNotIn('private-test-token', db.get(main.Task, task_id).result)

    def test_legacy_error_details_are_not_returned(self):
        import json
        secret = 'old-provider-private-token'
        with main.SessionLocal() as db:
            db.add(main.Task(id='legacy-error', prompt='Old', status='error', result=json.dumps({
                'error': secret, 'history': [{'status': 'error', 'description': secret}]})))
            db.commit()
        for path in ('/task_status/legacy-error', '/task_progress/legacy-error'):
            self.assertNotIn(secret, self.client.get(path).text)

    def test_model_styles_are_removed_before_reaching_clients(self):
        from src.security import sanitize_html
        payload = '<style>body{display:none}</style><p style="position:fixed!important;z-index:999999;background:url(https://example.com/track)">Finding</p>'
        cleaned = sanitize_html(payload)
        self.assertNotIn('<style', cleaned)
        self.assertNotIn('style=', cleaned)
        self.assertIn('<p>Finding</p>', cleaned)

    def test_unknown_task_and_config(self):
        self.assertEqual(self.client.get('/task_progress/missing').status_code, 404)
        self.assertIn('model', self.client.get('/config').json())

    def test_delete_finished_task_clears_record_and_progress(self):
        for status in ('done', 'error'):
            with self.subTest(status=status):
                with main.SessionLocal() as db:
                    db.add(main.Task(id='remove-me', prompt='Disposable test', status=status))
                    db.commit()
                main.task_progress['remove-me'] = {'steps': []}
                self.assertEqual(self.client.delete('/tasks/remove-me').status_code, 200)
                self.assertNotIn('remove-me', main.task_progress)
                self.assertEqual(self.client.get('/task_status/remove-me').status_code, 404)
                self.assertEqual(self.client.get('/task_progress/remove-me').status_code, 404)
                self.assertEqual(self.client.get('/tasks').json()['tasks'], [])

    def test_delete_running_task_rejected(self):
        with main.SessionLocal() as db:
            db.add(main.Task(id='running-task', prompt='Active test', status='running'))
            db.commit()
        self.assertEqual(self.client.delete('/tasks/running-task').status_code, 409)
        self.assertEqual(self.client.get('/task_status/running-task').json()['status'], 'running')

    def test_delete_missing_task(self):
        self.assertEqual(self.client.delete('/tasks/missing').status_code, 404)

    def test_batch_delete_finished_tasks(self):
        with main.SessionLocal() as db:
            db.add_all([main.Task(id='a', prompt='A', status='done'),
                        main.Task(id='b', prompt='B', status='error')])
            db.commit()
        main.task_progress.update({'a': {'steps': []}, 'b': {'steps': []}})
        response = self.client.post('/tasks/delete', json={'task_ids': ['a', 'b', 'a']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()['deleted']), {'a', 'b'})
        self.assertEqual(self.client.get('/tasks').json()['tasks'], [])
        self.assertEqual(main.task_progress, {})

    def test_batch_delete_is_atomic_when_running_or_missing(self):
        with main.SessionLocal() as db:
            db.add_all([main.Task(id='a', prompt='A', status='done'),
                        main.Task(id='b', prompt='B', status='running')])
            db.commit()
        self.assertEqual(self.client.post('/tasks/delete', json={'task_ids': ['a', 'b']}).status_code, 409)
        self.assertEqual(self.client.post('/tasks/delete', json={'task_ids': ['a', 'missing']}).status_code, 404)
        self.assertEqual(len(self.client.get('/tasks').json()['tasks']), 2)

    def test_empty_batch_delete_rejected(self):
        self.assertEqual(self.client.post('/tasks/delete', json={'task_ids': []}).status_code, 422)

    def test_all_private_endpoints_require_key_without_side_effects(self):
        with main.SessionLocal() as db:
            db.add(main.Task(id='private', prompt='Private research', status='done'))
            db.commit()
        anonymous = TestClient(main.app)
        anonymous.__enter__()
        self.addCleanup(anonymous.__exit__, None, None, None)
        endpoints = [('GET', '/config', None), ('GET', '/tasks', None),
                     ('GET', '/task_status/private', None), ('GET', '/task_progress/private', None),
                     ('DELETE', '/tasks/private', None),
                     ('POST', '/tasks/delete', {'task_ids': ['private']}),
                     ('POST', '/generate_report', {'prompt': 'Paid workflow'})]
        with patch.object(main.threading, 'Thread') as thread:
            for method, path, body in endpoints:
                with self.subTest(path=path):
                    response = anonymous.request(method, path, json=body)
                    self.assertIn(response.status_code, (401, 429))
                    self.assertNotIn('Private research', response.text)
            thread.assert_not_called()
        self.assertEqual(self.client.get('/task_status/private').status_code, 200)

    def test_wrong_key_cannot_delete_but_valid_key_can(self):
        with main.SessionLocal() as db:
            db.add(main.Task(id='private', prompt='Private', status='done'))
            db.commit()
        response = self.client.delete('/tasks/private', headers={'X-API-Key': 'wrong'})
        self.assertIn(response.status_code, (401, 429))
        self.assertEqual(self.client.delete('/tasks/private').status_code, 200)

    def test_paid_workflow_rate_and_queue_limits(self):
        with patch.dict(os.environ, {'RATE_LIMIT_PER_MINUTE': '1'}), patch.object(main.threading, 'Thread'):
            self.assertEqual(self.client.post('/generate_report', json={'prompt': 'First'}).status_code, 200)
            self.assertEqual(self.client.post('/generate_report', json={'prompt': 'Second'}).status_code, 429)
        from src.security import rate_events
        rate_events.clear()
        with patch.object(main, 'MAX_ACTIVE_TASKS', 1), patch.object(main.threading, 'Thread') as thread:
            self.assertEqual(self.client.post('/generate_report', json={'prompt': 'Overflow'}).status_code, 429)
            thread.assert_not_called()

    def test_legacy_and_new_model_html_is_sanitized(self):
        import json
        payload = '<h2>Finding</h2><img src=x onerror="alert(1)"><script>alert(2)</script><a href="javascript:alert(3)">bad</a>'
        with main.SessionLocal() as db:
            db.add(main.Task(id='legacy', prompt='Old', status='done', result=json.dumps({
                'html_report': payload, 'history': [{'title': 'research', 'substeps': [{'content': payload}]}]})))
            db.commit()
        for path in ('/task_status/legacy', '/task_progress/legacy'):
            body = self.client.get(path).text
            self.assertNotIn('onerror', body)
            self.assertNotIn('<script', body)
            self.assertNotIn('javascript:', body)
        with patch.object(main.threading, 'Thread'):
            task_id = self.client.post('/generate_report', json={'prompt': 'Test'}) .json()['task_id']
        with patch.object(main, 'executor_agent_step', return_value=('description', 'writer', payload)):
            main.plan_and_run(task_id, 'Test')
        with main.SessionLocal() as db:
            saved = db.get(main.Task, task_id).result
            self.assertNotIn('onerror', saved)
            self.assertNotIn('<script', saved)
        self.assertIn('Finding', saved)

    def test_sanitization_preserves_markdown_links_and_code_examples(self):
        from src.security import sanitize_html
        source = '# Sources\n\n<https://example.com/paper>\n\n[Reference](https://example.com/?a=1&b=2)\n\n```html\n<script>example()</script>\n```'
        rendered = sanitize_html(source)
        self.assertIn('href="https://example.com/paper"', rendered)
        self.assertIn('href="https://example.com/?a=1&amp;b=2"', rendered)
        self.assertIn('&lt;script&gt;example()&lt;/script&gt;', rendered)
        self.assertIn('<code', rendered)


if __name__ == '__main__':
    unittest.main()
