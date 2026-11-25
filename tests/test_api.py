import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

test_dir = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = 'sqlite:///' + test_dir.name.replace('\\', '/') + '/tasks.db'
os.environ.pop('RESET_DB_ON_STARTUP', None)
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
        self.client = TestClient(main.app)
        main.task_progress.clear()
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
        self.assertIn('Provider unavailable', task['result']['error'])

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


if __name__ == '__main__':
    unittest.main()
