# OnePlus follow-up — 2026-10-26

**Historical pre-fix results.** The subsequent [loader-order fix](LOADER-ORDER-FIX.md)
resolves the first-follow-up cache issue in two fresh-worker phone tests
(3.41–3.46s, 4,940 cached tokens, stable hashes). The GPU screening and synthetic
tool-reliability failures below remain valid; no GPU default was changed.

Updated the phone checkout from `13178a1` to `884878e`, then tested the new
schema guard and GPU controls on the actual device. **No faster GPU default was
validated.** The tool-schema guard works, but the first-follow-up cache acceptance
gate still fails. Model tool reliability needs more work before broad coding use.

Sanitized measurements: [performance-2026-10-26.json](../evidence/performance-2026-10-26.json).
Raw prompts, session identity, responses, server logs and resource samples remain
local under `qwen3.5-4b/logs/perf-20261026/` and ignored `validation-*.json` reports.
Historical evidence in `validation-summary.json` has not been overwritten.

## 1. Actual app: stable schemas, unstable first system prompt

Installed the updated personal profile using the backed-up merge installer.
No provider, cloud default, user instructions, or global settings were changed.
Fresh diagnostic workers were created through the production Pi Voice host;
existing workers were retained and the diagnostic workers closed afterward.

Two strict runs (`QWEN_REQUIRE_WARM_CACHE=1`) failed the cache gate:

| Run | Cold chat | First follow-up | Cached input on first follow-up |
| --- | ---: | ---: | ---: |
| Standard | 65.898 s | 65.669 s | 0 |
| Scoped payload capture | 65.830 s | 66.356 s | 0 |

Both arithmetic answers were correct. `toolsHash` was identical on both turns;
`systemHash` changed. Comparing the two captured synthetic-session requests found
only this system-prompt difference: the first includes the automatically generated
`subagents_enable` tool-list line; the next omits it. The tools arrays themselves
were identical. This explains why filtering serialized schemas alone is
insufficient. We did **not** freeze the system prompt or strip user instructions.

A separate full app run without the strict cache gate passed all five functional
checks using the restored **512/128** baseline:

| Check | Elapsed | Cached input in final assistant response |
| --- | ---: | ---: |
| Cold arithmetic | 204.083 s | 0 |
| First follow-up arithmetic | 188.657 s | 0 |
| Actual file read and result | 9.344 s | 5,171 |
| Arithmetic after tool use | 9.321 s | 4,886 |
| Second independently executed file read | 10.987 s | 5,438 |

File reads were verified against real tool calls and successful tool results in
the session log. Cached-input counters for tool turns describe the final response,
not the sum of both model requests. This functional pass is **not** a cache-gate
pass. The unexpectedly slow cold turns also demonstrate phone/runtime variability;
we did not establish whether scheduling, power management, or another factor caused
it. Do not advertise a fixed one-minute cold latency or a speedup from these runs.

## 2. GPU microbatch screening

Held model, backend, 8,192-token context, one slot, six generation/prefill threads,
checkpoint count four, snapshot RAM zero, fusion off, Flash Attention off and
thinking-off sampler constant. Runtime remained llama-cpp/Vulkan 0.7.0 with private
Turnip 26.2.5. Synthetic cold prompts contained 4,920 tokens.

| Batch / microbatch | Cold elapsed / first output | Prompt tok/s | Cached follow-up elapsed / first output | Result |
| --- | ---: | ---: | ---: | --- |
| 512 / 128 | 65.495 / 65.051 s | 75.65 | 2.532 / 2.074 s | FAIL: tool request |
| 512 / 256 | 104.388 / 103.906 s | 47.36 | 3.378 / 2.906 s | FAIL: tool request |
| 512 / 512 | 62.016 / 61.680 s | 79.80 | 2.610 / 2.286 s | FAIL: tool request |

The cached follow-up reused 4,916 tokens and evaluated 41 in each run. That is
strong evidence that preserving the prefix matters, not evidence of a new GPU
winner. The 512 microbatch's roughly 5% faster cold result is a single observation,
not a reliable improvement; its cached turn was slightly slower than baseline.
The 256 result was markedly slower, but attribution to batch size alone is unsafe.

All three failed identically on the subsequent `read` request: they listed values
from the reference table rather than calling the tool, stopping at the 512-token
limit. Fail-fast behavior prevented the simulated tool result, schema-change and
final decode cases. Failed completions are not valid throughput/quality wins.
No 1024-batch, thread-count, FA, fusion or driver experiments were attempted after
these failures; no candidate was promoted.

Resource observations, **not** full GPU memory or thermal measurements:

| Microbatch | Battery °C before → after | Peak process RSS KiB | Minimum MemAvailable KiB |
| --- | ---: | ---: | ---: |
| 128 | 34.1 → 34.3 | 1,179,792 | 3,063,624 |
| 256 | 35.3 → 35.3 | 1,195,164 | 3,672,816 |
| 512 | 35.3 → 35.0 | 1,225,940 | 3,671,692 |

Phone was unplugged. Samples were taken once a second. RSS excludes unaccounted
Vulkan/device allocations. Android thermal status was unavailable to unprivileged
Termux; battery temperature is not GPU temperature. Runs were single repetitions,
not randomized or thermally controlled, and are not a sustained-load comparison.

## 3. Tool failure diagnostics

To distinguish cache behavior from prompt/model behavior, replayed the identical
synthetic pre-tool history with `cache_prompt=false` on the baseline:

- **256 rows, 4,991 prompt tokens:** same table-listing failure, 512-token cutoff.
  Thus cache reuse is not necessary to reproduce this failure.
- **40 rows, 1,103 prompt tokens:** still listed table values, ending normally
  after 200 output tokens. This is not only an 8K capacity issue.
- **No table and no earlier retrieval history, 309 prompt tokens:** emitted the
  correct `read` call in 9.847 s (first meaningful tool output 7.667 s).
  No tool was executed in this protocol-only probe. Two variables changed here;
  it does not isolate table length from earlier task-switching history.
- **Full table/history, explicit reference-data delimiters and stronger tool
  instructions, 5,038 prompt tokens:** answered that the file does not exist
  without calling `read`. Clearer wording alone did not solve the problem.

These are synthetic failures, not a broad diagnosis of the model or proof that
all long contexts fail. The full personal app did execute both file reads.
Possible contributors include task-switching behavior, prompt design, the model,
or the pinned runtime/backend; no CPU or alternate-runtime comparison isolated
these. Preserve failures as regression fixtures rather than making expected
answers permissive.

## 4. Changes made during testing

- Fixed `benchmark-gpu.py` report writing: `Path.open()` does not accept `opener`.
  Reports now use built-in `open`, enforce mode 0600 (including existing files),
  and preserve the original failure rather than masking it with a writer error.
- Keep synthetic failed responses in local reports for diagnosis.
- Move exact repetitive copying to the end so it cannot prevent earlier tool
  measurements. It still fails the run if incorrect. An initial, pre-reorder
  baseline hit its 512-token limit on this case; that failure is not dismissed.
- Ensure the actual-app report marks the warm-cache check FAIL when its gate
  fails, rather than leaving an answer-correct PASS label on that failed check.
- Added report-writing/failure-retention and probe-order regression tests.

## Recommended next work / current usage

1. **Highest latency priority:** make loader activation and the generated tool
   section agree with the exact local profile before the first request. Fix the
   responsible extension/hook ordering, preserving user instructions and cloud
   behavior. Re-run the strict cache gate; require both stable hashes and actual
   cache reuse. This remains unresolved, not silently worked around.
2. **Keep the existing GPU defaults.** Only resume tuning after representative
   correctness checks pass, with repeated baseline/candidate runs under comparable
   power and temperature conditions. Do not infer a winner from these timings.
3. **Use Qwen for small, bounded tasks with verified results.** Reuse the session
   for related work when its prefix is stable. For unrelated tasks, a fresh bounded
   session is a reasonable hypothesis, with a cold-start cost—not a proven fix.
   Never accept an unexecuted file-existence/content claim as a tool result.
4. Add representative read → edit → test tasks and explicit task-switching tests;
   compare the pinned runtime against an identifiable alternate build or CPU on
   small fixtures. Keep thinking controls available and test harder tasks
   separately. Do not remove needed context or reduce quality to claim speed.
