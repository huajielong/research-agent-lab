import contextlib
import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src import agents, research_tools
import wikipedia.wikipedia as wiki


class ProviderSecurityTests(unittest.TestCase):
    def test_search_provider_exceptions_do_not_enter_model_context(self):
        diagnostic = 'endpoint=https://private.internal request=secret-test-token'
        with patch.object(research_tools.wikipedia, 'search', side_effect=RuntimeError(diagnostic)):
            self.assertNotIn('secret-test-token', str(research_tools.wikipedia_search_tool('Test')))
        with patch.dict(os.environ, {'TAVILY_API_KEY': 'test-only-key'}), \
             patch.object(research_tools, 'TavilyClient') as client:
            client.return_value.search.side_effect = RuntimeError(diagnostic)
            self.assertNotIn('secret-test-token', str(research_tools.tavily_search_tool('Test')))
        with patch.dict(os.environ, {'TAVILY_API_KEY': 'test-only-key'}), \
             patch.object(research_tools, 'TavilyClient', side_effect=RuntimeError(diagnostic)):
            self.assertNotIn('secret-test-token', str(research_tools.tavily_search_tool('Test')))
        with patch.object(research_tools.session, 'get', side_effect=research_tools.requests.RequestException(diagnostic)):
            self.assertNotIn('secret-test-token', str(research_tools.arxiv_search_tool('Test')))

    def test_research_exception_text_never_becomes_model_output(self):
        diagnostic = 'endpoint=https://private.internal request=secret-test-token'
        with patch.object(agents.client.chat.completions, 'create', side_effect=RuntimeError(diagnostic)), \
             contextlib.redirect_stdout(io.StringIO()):
            output, _ = agents.research_agent('Isolated test')
        self.assertTrue(output.startswith('[模型错误：'))
        self.assertNotIn('secret-test-token', output)
        self.assertNotIn('private.internal', output)

    def test_wikipedia_search_uses_https_transport(self):
        response = Mock()
        response.json.return_value = {'query': {'search': [{'title': 'Test article'}]}}
        research_tools.wikipedia.search.clear_cache()
        with patch.object(wiki.requests, 'get', return_value=response) as get, \
             patch.object(research_tools.wikipedia, 'page', return_value=SimpleNamespace(title='Test article', url='https://en.wikipedia.org/wiki/Test_article')), \
             patch.object(research_tools.wikipedia, 'summary', return_value='Test summary'):
            result = research_tools.wikipedia_search_tool('unique transport test')
        self.assertEqual(result[0]['summary'], 'Test summary')
        self.assertGreater(get.call_count, 0)
        for call in get.call_args_list:
            self.assertEqual(call.args[0], 'https://en.wikipedia.org/w/api.php')
            self.assertNotEqual(call.kwargs.get('verify'), False)
