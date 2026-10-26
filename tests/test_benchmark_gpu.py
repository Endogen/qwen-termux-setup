import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('benchmark_gpu', Path(__file__).resolve().parents[1] / 'qwen3.5-4b/benchmark-gpu.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def sse(*events):
    return [('data: ' + (event if isinstance(event, str) else json.dumps(event)) + '\n').encode() for event in events]


class StreamingTests(unittest.TestCase):
    def test_role_preamble_is_not_first_output_and_timings_survive_usage_tail(self):
        events = sse({'choices': [{'delta': {'role': 'assistant'}}]},
                     {'choices': [{'delta': {'content': '391'}}], 'timings': {'cache_n': 4000, 'prompt_n': 17}},
                     {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
                     {'choices': [], 'usage': {'completion_tokens': 4}}, '[DONE]')
        message, metrics = benchmark.collect_stream(events, 0, now=iter([2, 2, 3]).__next__)
        self.assertEqual(message['content'], '391')
        self.assertEqual(metrics['first_output_seconds'], 2)
        self.assertEqual(metrics['seconds'], 3)
        self.assertEqual(metrics['timings']['cache_n'], 4000)
        self.assertEqual(metrics['usage']['completion_tokens'], 4)

    def test_fragmented_tool_arguments_are_assembled_without_execution(self):
        events = sse({'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'fixture', 'function': {'name': 'read', 'arguments': '{"pa'}}]}}]},
                     {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': 'th":"/tmp/file"}'}}]}, 'finish_reason': 'tool_calls'}]}, '[DONE]')
        message, metrics = benchmark.collect_stream(events, 0, now=lambda: 1)
        self.assertEqual(json.loads(message['tool_calls'][0]['function']['arguments']), {'path': '/tmp/file'})
        self.assertEqual(metrics['first_output_seconds'], 1)
        self.assertIsNone(metrics['first_text_seconds'])

    def test_incomplete_and_error_streams_are_not_successes(self):
        for events in [sse({'choices': [{'delta': {'content': 'partial'}}]}),
                       sse({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}),
                       sse({'error': {'message': 'overflow'}})]:
            with self.assertRaises(RuntimeError):
                benchmark.collect_stream(events, 0, now=lambda: 1)


class ProbeTests(unittest.TestCase):
    def test_repetition_failure_keeps_prior_tool_measurements_and_fails_run(self):
        replies = [
            {'role': 'assistant', 'content': '1037'},
            {'role': 'assistant', 'content': '1019'},
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'fixture', 'function': {
                'name': 'read', 'arguments': '{"path":"/tmp/gpu-benchmark-fixture.txt"}'}}]},
            {'role': 'assistant', 'content': 'GPU_BENCHMARK_TOOL_OK'},
            {'role': 'assistant', 'content': '1023'},
            {'role': 'assistant', 'content': '1011'},
            {'role': 'assistant', 'content': 'incorrect repetition'},
        ]
        metrics = {'finish_reason': 'stop', 'reasoning_characters': 0,
                   'seconds': 1, 'first_output_seconds': 1}
        args = SimpleNamespace(url='http://unused', rows=40, repeats=1)
        report = {}
        with patch.object(benchmark, 'get', side_effect=[[{'is_processing': False}],
                         {'data': [{'id': 'qwen3.5-4b'}]}]), \
             patch.object(benchmark, 'post', side_effect=[(reply, metrics) for reply in replies]), \
             patch('builtins.print'):
            with self.assertRaisesRegex(RuntimeError, 'Correctness failure in warm_decode'):
                benchmark.run(args, report)
        self.assertEqual([row['case'] for row in report['runs']], [
            'cold_prefill', 'warm_followup', 'tool_call', 'tool_result',
            'changed_tools', 'stable_after_change', 'warm_decode'])
        self.assertTrue(all(row['correct'] for row in report['runs'][:-1]))
        self.assertFalse(report['runs'][-1]['correct'])
        self.assertEqual(report['runs'][-1]['response'], replies[-1])


class ReportTests(unittest.TestCase):
    def test_reports_are_written_privately_including_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nested' / 'report.json'
            for status in ('PASS', 'FAIL'):
                if path.exists():
                    path.chmod(0o644)
                benchmark.write_report(path, {'status': status})
                self.assertEqual(json.loads(path.read_text()), {'status': status})
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_main_preserves_failure_report_and_original_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            with patch('sys.argv', ['benchmark-gpu.py', '--run', '--label', 'test', '--output', str(path)]), \
                 patch.object(benchmark, 'run', side_effect=RuntimeError('Correctness failure')):
                with self.assertRaisesRegex(RuntimeError, 'Correctness failure'):
                    benchmark.main()
            report = json.loads(path.read_text())
            self.assertEqual(report['status'], 'FAIL')
            self.assertEqual(report['error'], 'Correctness failure')


if __name__ == '__main__':
    unittest.main()
