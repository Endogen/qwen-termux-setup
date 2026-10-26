import importlib.util
import json
from pathlib import Path
import unittest

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


if __name__ == '__main__':
    unittest.main()
