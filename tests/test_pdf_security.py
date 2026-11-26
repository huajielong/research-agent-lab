import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src import research_tools as tools


class PdfSecurityTests(unittest.TestCase):
    def setUp(self):
        self.dns = patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('151.101.3.42', 443))])
        self.dns.start()
        self.addCleanup(self.dns.stop)

    def response(self, url='https://arxiv.org/pdf/1234.5678.pdf', redirect=None, chunks=None):
        response = Mock(url=url, is_redirect=redirect is not None)
        response.headers = {'location': redirect} if redirect else {}
        response.iter_content.return_value = chunks or [b'%PDF-1.4 test']
        response.content = b'%PDF-1.4 test'
        response.raw = SimpleNamespace(_connection=SimpleNamespace(sock=Mock()))
        response.raw._connection.sock.getpeername.return_value = ('151.101.3.42', 443)
        return response

    def test_rejects_non_arxiv_and_ambiguous_urls_before_connecting(self):
        with patch.object(tools.session, 'get') as get:
            for url in ['http://127.0.0.1/x', 'http://169.254.169.254/latest', 'file:///etc/passwd',
                        'https://arxiv.org.evil.example/a', 'https://arxiv.org@evil.example/a',
                        'https://user:pass@arxiv.org/a', 'https://arxiv.org:8443/a',
                        'https://evil.arxiv.org/a', 'https://arxiv.org/\nfoo']:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    tools.fetch_pdf_bytes(url)
            get.assert_not_called()

    def test_rejects_private_dns_before_request(self):
        with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('10.0.0.1', 443))]), \
             patch.object(tools.session, 'get') as get:
            with self.assertRaises(ValueError):
                tools.fetch_pdf_bytes('https://arxiv.org/pdf/test.pdf')
            get.assert_not_called()

    def test_redirect_to_metadata_is_not_followed(self):
        response = self.response(redirect='http://169.254.169.254/latest')
        with patch.object(tools.session, 'get', return_value=response) as get:
            with self.assertRaises(ValueError):
                tools.fetch_pdf_bytes(response.url)
            self.assertEqual(get.call_count, 1)
            self.assertFalse(get.call_args.kwargs.get('allow_redirects', True))
            response.close.assert_called()

    def test_rejects_rebound_private_peer(self):
        response = self.response()
        response.raw._connection.sock.getpeername.return_value = ('127.0.0.1', 443)
        with patch.object(tools.session, 'get', return_value=response), \
             patch.dict('os.environ', {'NO_PROXY': '*', 'no_proxy': '*'}):
            with self.assertRaises(ValueError):
                tools.fetch_pdf_bytes(response.url)
            response.close.assert_called()

    def test_valid_public_arxiv_redirect_and_size_limit(self):
        first = self.response(redirect='/pdf/new.pdf')
        second = self.response(url='https://arxiv.org/pdf/new.pdf')
        with patch.object(tools.session, 'get', side_effect=[first, second]):
            self.assertEqual(tools.fetch_pdf_bytes(first.url), b'%PDF-1.4 test')
            first.close.assert_called()
            second.close.assert_called()
        response = self.response(chunks=[b'12345', b'67890'])
        with patch.object(tools.session, 'get', return_value=response), patch.object(tools, 'MAX_PDF_BYTES', 8, create=True):
            with self.assertRaises(ValueError):
                tools.fetch_pdf_bytes(response.url)
            response.close.assert_called()
