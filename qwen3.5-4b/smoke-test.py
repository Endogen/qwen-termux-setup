#!/data/data/com.termux/files/usr/bin/python
"""Test chat, streamed OpenAI tool calls, and a simulated tool-result round trip.

No proposed tool is executed. This does not contact or configure Pi.
Run while serve.sh is running. Optional first argument: output report JSON path.
"""
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

base = os.environ.get("QWEN_API_URL", "http://127.0.0.1:8081/v1").rstrip("/")
report = {"base_url": base, "tests": {}}


def post(payload, stream=False):
    payload = {
        "model": "qwen3.5-4b",
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "max_tokens": 256,
        "chat_template_kwargs": {"enable_thinking": False},
        **payload,
    }
    request = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=240) as response:
        if not stream:
            result = json.load(response)
        else:
            events = []
            for line in response:
                line = line.decode().strip()
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                events.append(json.loads(data))
            result = events
    return result, round(time.monotonic() - started, 3)


with urllib.request.urlopen(base + "/models", timeout=10) as response:
    models = json.load(response)
assert any(m["id"] == "qwen3.5-4b" for m in models["data"]), models
report["tests"]["model_listing"] = "PASS"

chat, elapsed = post({"messages": [
    {"role": "user", "content": "What is 17 multiplied by 23? Reply with the integer only."}
]})
text = chat["choices"][0]["message"].get("content", "")
assert text.strip() == "391", chat
report["tests"]["chat"] = {"status": "PASS", "seconds": elapsed, "response": chat}
print(f"PASS chat ({elapsed}s): {text.strip()}", flush=True)

fixture_path = "/tmp/pi-local-model-smoke-test.txt"
tools = [{"type": "function", "function": {
    "name": "read",
    "description": "Read the contents of a text file at the given path.",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string", "description": "Absolute file path"},
        "offset": {"type": "integer", "description": "Starting line number"},
        "limit": {"type": "integer", "description": "Maximum lines to read"},
    }, "required": ["path"]},
}}]
messages = [
    {"role": "system", "content": "Use the provided tool to read files. Never invent their contents."},
    {"role": "user", "content": f"Read {fixture_path} with the read tool, then tell me its contents."},
]
events, elapsed = post({"messages": messages, "tools": tools,
                         "tool_choice": "auto", "stream": True}, stream=True)
calls = {}
for event in events:
    for choice in event.get("choices", []):
        for delta in choice.get("delta", {}).get("tool_calls", []):
            call = calls.setdefault(delta["index"], {
                "id": "", "type": "function", "function": {"name": "", "arguments": ""},
            })
            if delta.get("id"):
                call["id"] = delta["id"]
            for key in ("name", "arguments"):
                call["function"][key] += delta.get("function", {}).get(key, "")
assert len(calls) == 1, events
call = next(iter(calls.values()))
assert call["id"], call
assert call["function"]["name"] == "read", call
assert json.loads(call["function"]["arguments"])["path"] == fixture_path, call
report["tests"]["streamed_tool_call"] = {
    "status": "PASS", "seconds": elapsed, "tool_call": call, "events": events,
}
print(f"PASS streamed tool call ({elapsed}s): {call['function']}", flush=True)

# Simulate a tool result. No filesystem tool or shell command is executed.
messages += [
    {"role": "assistant", "content": "", "tool_calls": [call]},
    {"role": "tool", "tool_call_id": call["id"], "content": "LOCAL_TOOL_TEST_OK"},
]
reply, elapsed = post({"messages": messages, "tools": tools, "tool_choice": "auto"})
message = reply["choices"][0]["message"]
assert "LOCAL_TOOL_TEST_OK" in (message.get("content") or ""), reply
assert not message.get("tool_calls"), reply
report["tests"]["tool_result_round_trip"] = {
    "status": "PASS", "seconds": elapsed, "response": reply,
}
print(f"PASS tool-result round trip ({elapsed}s): {message['content']}", flush=True)
report["status"] = "PASS"
if len(sys.argv) > 1:
    output = Path(sys.argv[1])
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {output}")
