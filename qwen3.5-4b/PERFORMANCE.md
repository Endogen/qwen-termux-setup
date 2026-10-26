# GPU latency work — 2026-10-26

The OnePlus is not connected during this revision. All new changes are prepared
and tested on the Mac; Android timings and quality validation remain pending.
Keep the existing Qwen3.5-4B Q5_K_M weights and private Turnip backend.

## What the evidence says

The existing server already offloads 33/33 layers to Adreno 840. The recorded
isolated GPU run processed prompts at 77.65 tokens/s and generated at 12.98
tokens/s; these are short runs, not sustained throughput guarantees. The full
app measured 76.311s for its first turn, 94.818s for the first follow-up after a
tool-list change, then 9.723–12.352s for cached turns. See
[validation-summary.json](../evidence/validation-summary.json) and
[GPU-INVESTIGATION.md](GPU-INVESTIGATION.md).

Thus preserving a valid prefix is a GPU optimization too: it avoids asking the
GPU to process thousands of unchanged tokens again. Faster generation alone
will barely help a 5,000-token prefill followed by a four-token answer.

## Cache behavior

llama.cpp normally reuses the matching token prefix and evaluates the suffix.
Tool declarations are serialized by the chat template, usually near the front.
Adding/removing/reordering tools or changing their descriptions can change that
prefix. It does **not** necessarily invalidate every token, but an early change
can make almost the whole conversation need evaluation again. Merely loading
software on disk does nothing to the cache unless the actual request changes.

Qwen3.5 also has hybrid/recurrent state. Reusing a prefix requires a compatible
saved state/checkpoint; matching text alone is not always sufficient. Check
server logs for checkpoint restoration and full-prompt-reprocessing messages.
Changing thinking mode, compaction, edited history, restarts, and another session
using the same single slot can also reduce reuse.

`--cache-ram 0` disables the additional RAM prompt-snapshot pool, **not** the
live slot's cache. The recorded warm turns already prove reuse with this value.
Four recurrent checkpoints are retained. Increasing RAM snapshots will not make
changed tokens reusable; do not allocate the default 8 GiB snapshot pool on this
16 GiB phone without measured memory headroom.

The compact profile now opts into `stableToolSchemas`: the serialized
OpenAI-completions request retains only its already-selected four tools, in
profile order. Current definitions are retained, absent tools are not invented,
and tool-free requests stay tool-free. The normal execution gate is retained.
Other models and APIs are unchanged. No instructions/history are trimmed.

Limit: this does not fix a loader adding/removing text in the system prompt.
The profile diagnostic reports `toolsHash`, `systemHash`, `toolsChanged`,
`systemChanged`, and removed tool names, without raw messages. Hashes describe
this hook's output; later provider hooks may still change it. A changed system
hash needs a scoped actual-payload inspection. Fix the responsible extension's
activation for this profile, or its hook ordering, rather than freezing old user
instructions or pretending the cache can safely ignore changed input.

## GPU experiment order

Change one variable per run; keep model, context (8192), one slot, sampler,
thinking mode, fusion off, Flash Attention off, and checkpoint count fixed.

| Order | Batch / microbatch | Generation / prefill threads | Purpose |
| --- | --- | --- | --- |
| Baseline | 512 / 128 | 6 / 6 | Reproduce validated configuration |
| 1 | 512 / 256 | 6 / 6 | Test better GPU utilization during prefill |
| 2 | 512 / 512 | 6 / 6 | Larger physical work units; watch peak memory |
| 3 | 1024 / 512 | 6 / 6 | Test larger logical batching independently |
| 4 | Best measured pair | 4 / 6, then 4 / 4 | Reduce CPU scheduling/thermal overhead if beneficial |

These are hypotheses, not recommended new defaults. Larger batches can consume
more scratch memory, fail, slow down, or change numerical behavior. More threads
are not automatically better when the GPU does most of the work. No CPU-core
affinity mask is assumed; inspect the phone's actual topology first.

Finish active requests, stop only the Qwen server, and start one candidate:

```sh
QWEN_BATCH=512 QWEN_UBATCH=256 QWEN_THREADS=6 QWEN_THREADS_BATCH=6 \
  ~/local-llm/qwen3.5-4b/serve.sh
```

In a second Termux terminal while the server is reserved for testing:

```sh
cd ~/local-llm
python qwen3.5-4b/benchmark-gpu.py --run \
  --label 'Turnip b512 ub256 t6 tb6 cp4 FAoff fusionoff' \
  --output qwen3.5-4b/logs/benchmark-b512-ub256.json
```

The benchmark replaces the active prefix cache. It never starts/stops a server,
executes tools, or contacts Pi. It checks the server is idle and single-slot, but
cannot reserve it against new app requests: leave the real app idle throughout.
It runs three repetitions of synthetic cold retrieval, a cached follow-up, a
longer output, streamed tool arguments plus a **simulated** tool result, a schema
change, and the next stable follow-up. Any correctness failure fails the run.
It records time to meaningful output (ignoring role-only SSE frames), time to
first answer text, elapsed time, usage, and server timings when supplied.
Missing cache/timing counters are unknown, not zero. Actual prompt length comes
from those counters; the synthetic fixture is not the full personal app prompt.

Record runtime/package identity, exact launcher settings, battery/charging state,
Android thermal status, peak process memory and available system RAM alongside
each report. Use comparable starting temperatures and repeat the baseline after
the candidates to expose thermal/order effects. Compare median first-output
latency, cold prefill rate, and generation rate separately. Then test a sustained
10–15 minute workload; a brief boost that throttles is not the winner.

Promote only candidates that pass the real app's multi-turn/tool sequence and
representative coding/reasoning tasks as well as the synthetic probe. Keep the
same model and reasoning options for the comparison. Numerical batching changes
need not produce byte-identical stochastic outputs; judge correctness and task
quality, including repetition, truncated reasoning, and real file-edit accuracy.

## Validate the profile in the real app

Install with the existing backed-up merge workflow after reviewing the changes:

```sh
python scripts/install-pi-config.py --replace-extension
python scripts/install-pi-config.py --apply --replace-extension
```

Use a new worker or reload an idle worker. For the acceptance run:

```sh
QWEN_REQUIRE_WARM_CACHE=1 \
QWEN_TEST_REPORT="$HOME/local-llm/qwen3.5-4b/validation-stable-cache.json" \
  node qwen3.5-4b/verify-actual-app.mjs
```

This runs the existing full personal-resource app sequence and additionally
requires some cache reuse on the first follow-up. It records profile hash
diagnostics alongside actual model usage. Some reuse is only a minimum gate:
inspect how much was reused and how much was reevaluated. A no-cache failure is
useful evidence, not a reason to relax the gate. Raw reports remain ignored.

## Later experiments, after the baseline

- **Runtime/Turnip upgrades:** obtain an identifiable llama.cpp commit (the saved
  package reports build 0/unknown), keep the current packages as rollback, and
  test a separately pinned candidate. Newer is not proof of faster or correct.
- **Flash Attention:** test `QWEN_FLASH_ATTN=on` in isolation, verify startup logs
  actually enable it, then repeat long-context and tool checks. The current
  validated setting is off. Do not combine it with a driver/fusion change.
- **Fusion / operation offload:** the launcher deliberately disables fusion and
  non-layer operation offload. Re-enable only in separate measured experiments;
  full layer offload does not mean every operation runs on the GPU. Previous
  failures do not isolate fusion as the cause, and absence of a crash is not a
  correctness test.
- **Checkpoint count:** compare 4 versus 8 only if stable prompts still rewind
  excessively and RAM allows it. Inspect actual restored positions. Do not
  disable checkpoints as a speed shortcut for this recurrent model.
- **Speculative decoding:** potentially useful for generation, but backend/model
  support, draft overhead and acceptance rate must be measured. It is secondary
  to the observed prefill problem and not enabled by this revision.

Do not lower weight/KV precision, shrink the usable context, truncate tool results,
force shorter reasoning, or remove needed instructions to claim a quality-neutral
speedup. Preserve the currently available thinking controls for difficult tasks.

## Upstream references checked

- [llama.cpp server parameters and cache semantics](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [llama.cpp cache/checkpoint implementation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/server-context.cpp)
- [Pi extension request lifecycle](https://pi.dev/docs/latest/extensions)

The Pi 1.4.0 npm package's runner was also inspected: `before_provider_request`
returns the replacement payload directly, and handlers run in extension order.
Current upstream docs are not a guarantee that every newer flag exists in the
pinned Termux package; check its `llama-server --help` before using new flags.
