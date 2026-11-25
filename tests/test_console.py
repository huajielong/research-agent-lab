import io
import unittest

from src.console import configure_console


class ConsoleTests(unittest.TestCase):
    def test_gbk_stream_preserves_chinese_and_escapes_emoji(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding='gbk', errors='strict')
        configure_console(stream, None)
        print('🔍 研究智能体：检索 🧠', file=stream, flush=True)
        value = buffer.getvalue().decode('gbk')
        self.assertIn('研究智能体：检索', value)
        self.assertIn('\\U0001f50d', value)

    def test_utf8_stream_keeps_emoji(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding='utf-8')
        configure_console(stream, None)
        print('🔍 中文', file=stream, flush=True)
        self.assertEqual(buffer.getvalue().decode('utf-8').splitlines(), ['🔍 中文'])

    def test_redirected_stream_without_reconfigure(self):
        stream = io.StringIO()
        configure_console(stream, None)
        print('🔍 中文', file=stream)
        self.assertIn('🔍', stream.getvalue())
