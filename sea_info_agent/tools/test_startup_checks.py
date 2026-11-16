"""API 시작 실패의 빠른 안내와 인증정보 비노출을 확인한다."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from tools import runtools


class StartupTests(unittest.TestCase):
    def test_health_check_uses_ipv4_and_allows_remote_db_connection_time(self):
        import httpx
        with patch.object(runtools,'_env'),patch.dict(runtools.os.environ,{'API_PORT':'8000'}), \
             patch.object(httpx,'get',return_value=Mock(status_code=200,json=lambda:{'db':True})) as get:
            self.assertEqual(runtools.api_health(),{'db':True})
            get.assert_called_once_with('http://127.0.0.1:8000/health',timeout=10)

    def test_db_session_limit_has_safe_specific_message(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'logs').mkdir()
            (root/'logs/api.log').write_text('PRIVATE_CONNECTION_STRING\nEMAXCONNSESSION max clients reached\nApplication startup failed',encoding='utf-8')
            with patch.object(runtools,'ROOT',root):message=runtools.api_startup_failure()
            self.assertIn('동시 연결 한도',message)
            self.assertNotIn('PRIVATE_CONNECTION_STRING',message)

    def test_incomplete_startup_is_not_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'logs').mkdir()
            (root/'logs/api.log').write_text('Waiting for application startup.',encoding='utf-8')
            with patch.object(runtools,'ROOT',root):self.assertEqual(runtools.api_startup_failure(),'')

    def test_wait_stops_immediately_after_startup_failure(self):
        output=StringIO()
        with patch.object(runtools.sys,'argv',['runtools.py','wait-api','60']), \
             patch.object(runtools,'api_health',return_value=None) as health, \
             patch.object(runtools,'api_startup_failure',return_value='DB 연결 실패'), \
             patch.object(runtools.time,'sleep') as sleep,redirect_stdout(output):
            self.assertEqual(runtools.main(),1)
        health.assert_called_once();sleep.assert_not_called()
        self.assertIn('DB 연결 실패',output.getvalue())


if __name__=='__main__':unittest.main()
