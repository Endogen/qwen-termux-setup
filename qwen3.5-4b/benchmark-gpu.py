#!/usr/bin/env python3
"""Sequential GPU/cache latency probe. No tools are executed and Pi is not contacted.

Run only while the phone's Qwen server is reserved for this test: the cold request
and schema-change request replace its active prefix cache. Defaults match Pi's
validated thinking-off sampler. This is a functional probe, not a coding eval.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import time
import urllib.request


READ = {"type": "function", "function": {
    "name": "read", "description": "Read the contents of a text file.",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
}}
EXTRA = {"type": "function", "function": {
    "name": "lookup", "description": "Look up an optional reference.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}}


def collect_stream(lines, started, now=time.monotonic):
    text, reasoning, calls = [], [], {}
    first_output = first_text = None
    usage, timings, finish = {}, {}, None
    done = False
    for line in lines:
        line = line.decode().strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            done = True
            break
        event = json.loads(data)
        if event.get("error"):
            raise RuntimeError(str(event["error"]))
        usage = event.get("usage") or usage
        timings = event.get("timings") or timings
        for choice in event.get("choices", []):
            delta = choice.get("delta", {})
            content = delta.get("content") or ""
            thought = delta.get("reasoning_content") or ""
            tool_deltas = delta.get("tool_calls") or []
            meaningful_tool = any(d.get("function", {}).get("name") or d.get("function", {}).get("arguments") for d in tool_deltas)
            if (content or thought or meaningful_tool) and first_output is None:
                first_output = now() - started
            if content and first_text is None:
                first_text = now() - started
            text.append(content)
            reasoning.append(thought)
            for delta_call in tool_deltas:
                call = calls.setdefault(delta_call["index"], {
                    "id": "", "type": "function", "function": {"name": "", "arguments": ""},
                })
                if delta_call.get("id"):
                    call["id"] = delta_call["id"]
                for field in ("name", "arguments"):
                    call["function"][field] += delta_call.get("function", {}).get(field) or ""
            finish = choice.get("finish_reason") or finish
    if not done or finish is None:
        raise RuntimeError("Incomplete SSE response")
    message = {"role": "assistant", "content": "".join(text)}
    if calls:
        message["tool_calls"] = [calls[i] for i in sorted(calls)]
    return message, {
        "seconds": now() - started, "first_output_seconds": first_output,
        "first_text_seconds": first_text, "finish_reason": finish,
        "reasoning_characters": len("".join(reasoning)), "usage": usage, "timings": timings,
    }


def get(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def post(base, messages, tools, cache):
    body = {"model": "qwen3.5-4b", "messages": messages, "tools": tools,
            "tool_choice": "auto", "stream": True, "stream_options": {"include_usage": True},
            "cache_prompt": cache, "temperature": 0.2, "top_p": 0.8, "top_k": 20,
            "min_p": 0, "presence_penalty": 0, "repeat_penalty": 1,
            "max_tokens": 512, "seed": 1234, "chat_template_kwargs": {"enable_thinking": False}}
    request = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=600) as response:
        return collect_stream(response, started)


def run(args, report):
    base = args.url.rstrip("/")
    slots = get(base + "/slots")
    if not isinstance(slots, list) or any(s.get("is_processing") for s in slots):
        raise RuntimeError("Server must be idle; do not interrupt another request")
    if len(slots) != 1:
        raise RuntimeError("This comparison requires the validated single-slot server")
    models = get(base + "/v1/models")
    if not any(m.get("id") == "qwen3.5-4b" for m in models.get("data", [])):
        raise RuntimeError("Expected qwen3.5-4b server")
    table = "\n".join(f"Record {i:04d}: category blue; value {1000 + i}." for i in range(args.rows))
    report["runs"] = []
    for repeat in range(args.repeats):
        messages = [{"role": "system", "content":
                     "Answer questions from this reference table exactly. Read files with read; never invent their contents.\n" + table}]
        tools = [READ]

        def turn(name, prompt=None, expected=None, cache=True, expect_read=False):
            if prompt is not None:
                messages.append({"role": "user", "content": prompt})
            message, timing = post(base, messages, tools, cache)
            messages.append(message)
            if expect_read:
                calls = message.get("tool_calls", [])
                try:
                    correct = (len(calls) == 1 and bool(calls[0]["id"]) and
                               calls[0]["function"]["name"] == "read" and
                               json.loads(calls[0]["function"]["arguments"]).get("path") == "/tmp/gpu-benchmark-fixture.txt")
                except (ValueError, KeyError):
                    correct = False
            else:
                correct = message["content"].strip() == expected and not message.get("tool_calls")
            correct = correct and timing["finish_reason"] != "length" and timing["reasoning_characters"] == 0
            row = {"repeat": repeat + 1, "case": name, "correct": correct, **timing}
            report["runs"].append(row)
            print(json.dumps(row), flush=True)
            if not correct:
                raise RuntimeError(f"Correctness failure in {name}; do not promote this configuration")
            return message

        turn("cold_prefill", "What is the value of record 0037? Reply with the integer only. Do not use tools.", "1037", cache=False)
        turn("warm_followup", "What is the value of record 0019? Reply with the integer only. Do not use tools.", "1019")
        copied = " ".join(["red green blue yellow"] * 24)
        turn("warm_decode", f"Copy the following text exactly, on one line, without quotes or tools:\n{copied}", copied)
        call = turn("tool_call", "Read /tmp/gpu-benchmark-fixture.txt with read. Then reply only with its contents.", expect_read=True)
        messages.append({"role": "tool", "tool_call_id": call["tool_calls"][0]["id"], "content": "GPU_BENCHMARK_TOOL_OK"})
        turn("tool_result", expected="GPU_BENCHMARK_TOOL_OK")
        tools = [READ, EXTRA]
        turn("changed_tools", "What is the value of record 0023? Reply with the integer only. Do not use tools.", "1023")
        turn("stable_after_change", "What is the value of record 0011? Reply with the integer only. Do not use tools.", "1011")
    report["medians"] = {}
    for case in dict.fromkeys(row["case"] for row in report["runs"]):
        rows = [row for row in report["runs"] if row["case"] == case]
        report["medians"][case] = {field: statistics.median(row[field] for row in rows if row[field] is not None)
                                    for field in ("seconds", "first_output_seconds") if any(row[field] is not None for row in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Confirm the server is reserved; this test replaces its prefix cache")
    parser.add_argument("--url", default="http://127.0.0.1:8081")
    parser.add_argument("--label", required=True, help="Record backend, batch/ubatch, threads, and checkpoint settings")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--rows", type=int, default=256, help="Synthetic prefill length; actual token counts come from server metrics")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.run:
        parser.error("Use --run only when Qwen is idle and reserved for benchmarking")
    if not 1 <= args.repeats <= 20 or not 40 <= args.rows <= 300:
        parser.error("repeats must be 1..20 and rows 40..300")
    report = {"label": args.label, "rows": args.rows, "repeats": args.repeats,
              "scope": "Synthetic sequential latency/correctness probe; simulated tool result; no tools executed",
              "status": "FAIL"}
    try:
        run(args, report)
        report["status"] = "PASS"
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", opener=lambda path, flags: os.open(path, flags, 0o600)) as output:
            json.dump(report, output, indent=2)
            output.write("\n")
        print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
