"""Exercise actual comment CLI against a local HTTP service, no production writes.

Comment bodies are synthetic transport fixtures; embedded reproduction commands
are not executed and their sample results are not GPU or model-behavior tests.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/comment.py'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, data):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if '/comment/1' in self.path:
            self.send(200, {'id': '1', 'body': 'mismatch' if self.server.mismatch else self.server.items[0]['body']})
            return
        self.send(self.server.readstatus, {'startAt': 0, 'total': len(self.server.items), 'comments': self.server.items})

    def do_POST(self):
        if self.headers.get('Authorization') != 'Bearer offline-comment-token':
            self.send(401, {})
            return
        value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.posts += 1
        self.server.items.append({'id': '1', 'body': value['body']})
        self.send(self.server.poststatus, {'id': '1'} if self.server.poststatus == 201 else {})


class Comments(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.items = []
        self.server.posts = 0
        self.server.readstatus = 200
        self.server.poststatus = 201
        self.server.mismatch = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        origin = f'http://127.0.0.1:{self.server.server_port}'
        (self.root / 'token').write_text('offline-comment-token')
        (self.root / 'body').write_text('Conclusion\nEvidence\nNext step')
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('JIRA_')}
        self.env.update(JIRA_BASE_URL=origin, JIRA_ALLOWED_ORIGIN=origin, JIRA_TOKEN_FILE=str(self.root / 'token'),
                        JIRA_COMMENT_ISSUE='TEST-1', NO_PROXY='127.0.0.1', no_proxy='127.0.0.1')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def call(self, issue='TEST-1', options=()):
        run = subprocess.run([sys.executable, '-B', str(SCRIPT), '--issue-id', issue, '--session', 'session-1',
                              '--event', 'conclusion', '--body-file', str(self.root / 'body'), *options],
                             env=self.env, capture_output=True, text=True, timeout=10)
        self.stderr = run.stderr
        self.assertNotIn('offline-comment-token', run.stdout + run.stderr)
        value = json.loads(run.stdout)
        self.assertEqual(run.returncode, 0 if value['success'] else 1)
        return value

    def test_publish_verify_and_reuse(self):
        self.assertTrue(self.call()['verified'])
        self.assertTrue(self.call()['reused'])
        self.assertEqual(self.server.posts, 1)

    def test_other_issue_rejected(self):
        self.assertFalse(self.call('TEST-2')['success'])
        self.assertEqual(self.server.posts, 0)

    def test_changed_event_body_does_not_duplicate(self):
        self.call()
        (self.root / 'body').write_text('changed')
        self.assertFalse(self.call()['success'])
        self.assertEqual(self.server.posts, 1)

    def test_incomplete_history_prevents_write(self):
        self.server.readstatus = 403
        self.assertFalse(self.call()['success'])
        self.assertEqual(self.server.posts, 0)

    def test_readback_failure_not_reported_as_success(self):
        self.server.mismatch = True
        self.assertFalse(self.call()['success'])

    def test_server_commit_with_error_recovered_without_duplicate(self):
        self.server.poststatus = 503
        self.assertFalse(self.call()['success'])
        self.assertTrue(self.call()['reused'])
        self.assertEqual(self.server.posts, 1)

    def test_credential_body_rejected(self):
        (self.root / 'body').write_text('offline-comment-token')
        self.assertFalse(self.call()['success'])
        self.assertEqual(self.server.posts, 0)

    def test_short_comment_has_no_length_warning(self):
        body = ('本轮结论：已定位到 loader.py/read_record，空记录被误读为结束。\n'
                '分析依据：空行后的记录丢失。Python 3.10，下载 [复现脚本|http://127.0.0.1/reproduce.py] 后运行：\n'
                '{code:bash}\npython3 reproduce.py --mode original\npython3 reproduce.py --mode fixed\n{code}\n'
                '结果：原分支少读一条，修正分支全部读入。\n'
                '后续处理：由应用维护者评审修正。\n'
                '报告与附件：[报告|http://127.0.0.1/report]。')
        (self.root / 'body').write_text(body)
        value = self.call()
        self.assertTrue(value['verified'])
        self.assertEqual(value['warnings'], [])
        self.assertEqual(self.stderr, '')
        self.assertEqual(self.server.items[0]['body'], body + '\n[hpc-agent:TEST-1:session-1:conclusion]')

    def test_conclusion_preserves_location_and_unverified_scope(self):
        body = ('本轮结论：已定位到 loader.py/read_record，空记录被误读为结束标记。\n'
                '分析依据：空行后记录丢失；Python 3.10，下载 [脚本|http://127.0.0.1/reproduce.py] 后运行：\n'
                '{code:bash}\npython3 reproduce.py --mode original\npython3 reproduce.py --mode fixed\n{code}\n'
                '结果：原分支失败，修正分支通过。\n'
                '环境说明：指定平台镜像无法取得，当前只有本机镜像，本次要求的跨平台回验受阻。\n'
                '后续处理：取得所需镜像后回验原案例。\n'
                '报告与附件：[报告|http://127.0.0.1/report]。')
        (self.root / 'body').write_text(body)
        value = self.call(options=('--warn-chars', '100'))
        self.assertTrue(value['verified'])
        self.assertEqual(value['warnings'][0]['characters'], len(body))
        self.assertEqual(self.server.items[0]['body'], body + '\n[hpc-agent:TEST-1:session-1:conclusion]')
        self.assertTrue(self.call()['reused'])
        self.assertEqual(self.server.posts, 1)

    def test_environment_only_comment_reconciles_without_duplicate(self):
        body = ('本轮结论：原问题尚未定位，复现被当前容器环境阻塞。\n'
                '环境说明：宿主 C500 可见，但容器中 /dev/mxcd 不存在，设备检查失败，原测试未能启动；需要正确的设备映射。\n'
                '后续处理：补齐映射后重试原命令；报告上传返回 503，待对账后交付。')
        (self.root / 'body').write_text(body)
        self.server.poststatus = 503
        self.assertFalse(self.call()['success'])
        value = self.call()
        self.assertTrue(value['verified'])
        self.assertTrue(value['reused'])
        self.assertEqual(self.server.posts, 1)
        self.assertEqual(self.server.items[0]['body'], body + '\n[hpc-agent:TEST-1:session-1:conclusion]')

    def test_existing_steps_reference_without_optional_fields_is_preserved(self):
        body = ('本轮结论：原案例回验通过。\n'
                '分析依据：三轮输出均与批准的参考一致，方法与配置沿用 [原复现步骤|http://127.0.0.1/comment/7]。')
        (self.root / 'body').write_text(body)
        self.assertTrue(self.call()['verified'])
        self.assertTrue(self.call()['reused'])
        self.assertEqual(self.server.posts, 1)
        self.assertEqual(self.server.items[0]['body'], body + '\n[hpc-agent:TEST-1:session-1:conclusion]')

    def test_handoff_chain_preserves_multiline_steps_and_results(self):
        body = ('本轮结论：已定位到解析库的空记录分支，需维护方修正。\n'
                '分析依据：原应用在空记录后的数据丢失；Python 3.10，下载 [复现脚本|http://127.0.0.1/probe.py] 后执行：\n'
                '{code:bash}\n'
                'python3 probe.py --scope application --revision original\n'
                'python3 probe.py --scope parser --revision original\n'
                'python3 probe.py --scope parser --revision fixed\n'
                'python3 probe.py --scope application --revision fixed\n'
                '{code}\n'
                '结果：原应用与解析探针均少读一条；仅修正空记录分支后，两者全部读入。其它输入与配置保持不变。'
                '接手方按上述四步可分别复核原现象、组件归因及原案例修正效果。\n'
                '后续处理：解析库维护方评审修正并执行回归测试。\n'
                '报告与附件：[完整实验报告|http://127.0.0.1/report]。')
        (self.root / 'body').write_text(body)
        value = self.call()
        self.assertTrue(value['verified'])
        self.assertEqual(value['warnings'][0]['characters'], len(body))
        self.assertTrue(self.call()['reused'])
        self.assertEqual(self.server.posts, 1)
        self.assertEqual(self.server.items[0]['body'], body + '\n[hpc-agent:TEST-1:session-1:conclusion]')

    def test_nested_lists_commands_and_explanations_are_preserved(self):
        body = ('本轮结论：已定位到解析库遇空行后提前结束。\n\n'
                '分析依据：\n'
                '* 原分支漏读空行后的记录。\n'
                '** 输入中有两条有效记录；空行不应终止读取。\n'
                '* 只修正结束条件后，两条记录均被读取。\n\n'
                '1. 在已准备的测试容器 /case 目录执行原分支。\n'
                '{code:bash}\n'
                'cd /case\n'
                'task_input="/case/input data.csv"\n'
                'python3 probe.py --input "$task_input" --mode original\n'
                'task_result=$?\n'
                'printf "probe_exit=%s\\n" "$task_result"\n'
                '{code}\n\n'
                '2. 用同一输入检查修正分支。\n'
                '{code:bash}\n'
                'python3 probe.py --input "$task_input" --mode fixed\n'
                '{code}\n\n'
                '* 结果说明：原分支只读取一条记录，漏读问题被触发。\n'
                '** observed 是实际读取数，expected 是输入中的有效记录数。\n'
                '** 该探针退出 1 表示检测到漏读；准备失败不计为故障复现。\n'
                '{noformat}\nobserved=1 expected=2\nprobe_exit=1\n{noformat}')
        (self.root / 'body').write_text(body)
        value = self.call()
        self.assertTrue(value['verified'])
        self.assertEqual(value['warnings'][0]['characters'], len(body))
        self.assertEqual(self.server.items[0]['body'], body + '\n[hpc-agent:TEST-1:session-1:conclusion]')
        self.assertTrue(self.call()['reused'])
        self.assertEqual(self.server.posts, 1)

    def test_long_unwrapped_command_is_preserved(self):
        command = 'python3 /case/probe.py ' + ' '.join(f'--input /case/data/part-{index:03d}.csv' for index in range(40))
        body = ('本轮结论：已观察到原输入缺行。\n\n'
                '分析依据：在 /case 中执行完整输入测试。\n'
                '{code:bash}\n' + command + '\n{code}\n'
                '结果说明：输入分片均被打开，但空行后的记录未进入输出。')
        (self.root / 'body').write_text(body)
        value = self.call()
        self.assertTrue(value['verified'])
        self.assertEqual(value['warnings'][0]['characters'], len(body))
        self.assertEqual(self.server.items[0]['body'], body + '\n[hpc-agent:TEST-1:session-1:conclusion]')
        self.assertTrue(self.call()['reused'])
        self.assertEqual(self.server.posts, 1)

    def test_threshold_excludes_marker_and_outer_whitespace(self):
        body = '证' * 350
        (self.root / 'body').write_text('\n' + body + '\n')
        self.assertEqual(self.call()['warnings'], [])
        self.assertEqual(self.server.items[0]['body'], body + '\n[hpc-agent:TEST-1:session-1:conclusion]')

    def test_long_comment_warns_without_changing_evidence(self):
        body = '分析依据\n' * 80 + '未知项：尚未验证原环境。'
        (self.root / 'body').write_text(body)
        value = self.call()
        self.assertTrue(value['verified'])
        self.assertEqual(value['warnings'], [{'code': 'comment_length_advisory',
                                             'characters': len(body), 'threshold': 350}])
        self.assertEqual(json.loads(self.stderr)['warning'], value['warnings'][0])
        self.assertNotIn('分析依据', self.stderr)
        self.assertEqual(self.server.items[0]['body'], body + '\n[hpc-agent:TEST-1:session-1:conclusion]')

    def test_previous_hard_limit_is_advisory_only(self):
        body = '证' * 4001 + '\n尾部证据不得截断'
        (self.root / 'body').write_text(body)
        value = self.call()
        self.assertTrue(value['verified'])
        self.assertEqual(value['warnings'][0]['characters'], len(body))
        self.assertEqual(self.server.items[0]['body'], body + '\n[hpc-agent:TEST-1:session-1:conclusion]')

    def test_warning_threshold_is_configurable_without_changing_event(self):
        body = '证' * 351
        (self.root / 'body').write_text(body)
        self.assertEqual(self.call()['warnings'][0]['threshold'], 350)
        value = self.call(options=('--warn-chars', '500'))
        self.assertEqual(value['warnings'], [])
        self.assertTrue(value['reused'])
        self.assertEqual(self.server.posts, 1)

    def test_long_comment_retry_is_deduplicated(self):
        (self.root / 'body').write_text('证' * 500)
        self.assertTrue(self.call()['verified'])
        self.assertTrue(self.call()['reused'])
        self.assertEqual(self.server.posts, 1)

    def test_invalid_warning_threshold_prevents_write(self):
        for threshold in ('0', '-1'):
            with self.subTest(threshold=threshold):
                value = self.call(options=('--warn-chars', threshold))
                self.assertEqual(value['error'], 'invalid_warning_threshold')
        self.assertEqual(self.server.posts, 0)

    def test_long_comment_does_not_bypass_safety_checks(self):
        (self.root / 'body').write_text('证' * 500)
        self.assertFalse(self.call('TEST-2')['success'])
        for suffix in ('offline-comment-token', '[hpc-agent:forged]'):
            with self.subTest(suffix=suffix):
                (self.root / 'body').write_text('证' * 500 + suffix)
                self.assertFalse(self.call()['success'])
        self.assertEqual(self.server.posts, 0)


if __name__ == '__main__':
    unittest.main()
