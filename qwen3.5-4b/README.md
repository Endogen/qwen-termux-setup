# Qwen3.5-4B local preparation for Pi

Qwen is registered as the optional `local-llama/qwen3.5-4b` model in
`~/.pi/agent/models.json`. Pi's default model and credentials are unchanged.
An opt-in, model-specific compact tool profile and compaction budgets are
configured for Qwen; other models keep their ordinary settings and tools.
There is no boot service or automatic startup.

## Model and runtime

- Model: Qwen/Qwen3.5-4B, text-only use (no vision projector downloaded).
- Quantization: Unsloth Q5_K_M; 3,143,656,608 bytes (~3.14 GB).
- Full pinned source URL and SHA-256: model-info.json.
- Android's original download: /storage/emulated/0/Download/Qwen3.5-4B-Q5_K_M.gguf
- Runtime copy: this directory's Qwen3.5-4B-Q5_K_M.gguf. Private Termux
  storage avoids Android shared-storage overhead during inference.
- Termux packages: llama-cpp 0.7.0, llama-cpp-backend-vulkan,
  vulkan-loader-android, vulkan-loader.

## Start manually

From Termux:

```sh
~/local-llm/qwen3.5-4b/serve.sh
```

This is a foreground process; stop with Ctrl+C. To leave it running in its
own tmux server instead (separate from Pi Voice's managed sessions):

```sh
tmux -L qwen-local new-session -d -s qwen3-5-4b \
  'exec ~/local-llm/qwen3.5-4b/serve.sh'
```

Attach with `tmux -L qwen-local attach -t qwen3-5-4b`; detach with Ctrl+B then D.
Stop just Qwen with `tmux -L qwen-local kill-session -t qwen3-5-4b`.
It is not automatically started when Termux, Android, or Pi starts. Android
may suspend or terminate Termux when backgrounded; this is not a permanently
running service.

The launcher now defaults to the validated **private Mesa Turnip GPU backend**
(full 33/33 layer offload), one request slot, 8,192 tokens of total context, and
six threads for remaining CPU work. Android's driver is not replaced. CPU remains
an explicit fallback: `QWEN_BACKEND=cpu ~/local-llm/qwen3.5-4b/serve.sh`.
Stop only the existing Qwen server while idle before changing backends; do not run
two servers on port 8081. Prompt, history, reasoning, tool results, and
reply must share that context. Default maximum generation is 2,048 tokens;
a client may request another budget that fits the remaining context.

Thinking uses the model template's default. A compatible client can pass
`chat_template_kwargs: {"enable_thinking": false}` for direct responses.
The server separates reasoning into `reasoning_content` rather than mixing
it into the final answer. Thinking can consume hundreds of tokens before any
final answer; a 512-token generation cap was exhausted during one unbudgeted
arithmetic test. A bounded 128-token thinking test did return the correct final
answer with separate reasoning (~19 seconds total). To use that bounded profile:

```sh
~/local-llm/qwen3.5-4b/serve.sh --reasoning-budget 128
```

This is a latency/quality trade-off, not a guarantee that difficult reasoning
will fit into 128 tokens. The standard launcher leaves thinking unrestricted
within the request's generation/context limits.

Standalone server sampling defaults are temperature 1.0, top_p 0.95,
top_k 20, min_p 0, presence_penalty 1.5, repeat_penalty 1.0. Pi's local-model
thinking-off profile now overrides temperature to **0.2**, top_p to **0.8**, and
presence_penalty to **0**. This conservative preset passed the complete normal-app
chat/tool sequence; the previous 0.7/1.5 preset sometimes repeated answers or tool
calls. Cloud models and thinking-on sampling were not changed.

CPU mode was validated at approximately 8-9 generated tokens/second in short
non-thinking tests. Cold prompt processing was approximately 11-12 tokens/second:
large tool schemas and long initial prompts can take noticeably longer than the
reply itself. These are short smoke-test measurements, not sustained benchmarks.

The installed Vulkan backend with the **stock Qualcomm driver** detects Adreno
840, but produces incorrect repetitive output, even with FP16 disabled and Flash
Attention off. The launcher blocks that raw mode unless both QWEN_BACKEND=vulkan
and QWEN_ALLOW_UNVERIFIED_VULKAN=1 are set; that override is only for debugging.

A later driver A/B investigation found a working alternative: **Mesa Turnip
26.2.5**, extracted into this directory without replacing Android's driver or
installing a different global Vulkan loader. It passed full-GPU chat, streamed
tool calls, actual Pi tool execution, and a 5,057-token retrieval test. The GPU
backend is now **active at the original endpoint and the launcher default**.
Final settings disable fusion and Flash Attention, disable RAM prompt snapshots,
and retain at most four recurrent context checkpoints. Pi uses the conservative
thinking-off sampling preset above. No APK rebuild or boot autostart was added.
See **GPU-INVESTIGATION.md** and its activation update for all evidence and caveats.

Smaller context for standalone protocol tests only (this is too small for
the current real Pi profile, whose measured input was 5,078 tokens):

```sh
QWEN_CONTEXT=4096 ~/local-llm/qwen3.5-4b/serve.sh
```

Other overrides: QWEN_PORT (8081), QWEN_THREADS (6), QWEN_REASONING
(auto/on/off), QWEN_FLASH_ATTN (auto for CPU, off for Turnip), and Turnip-only
QWEN_CACHE_MB (0) and QWEN_CONTEXT_CHECKPOINTS (4). Additional llama-server arguments can be supplied after the
script name. These are runtime overrides, not Pi configuration.

GPU tuning also exposes `QWEN_BATCH` (512), `QWEN_UBATCH` (128), and
`QWEN_THREADS_BATCH` (defaults to `QWEN_THREADS`). Defaults are unchanged.
See [PERFORMANCE.md](PERFORMANCE.md) for the controlled experiment order,
cache explanation, and a benchmark that measures first-output latency separately
from total completion time.

## Pi integration -- configured and verified

Create a **fresh Pi session** after this configuration was installed, then use
Pi Voice Settings → Connected Pi → Model and choose
**Qwen3.5 4B Q5_K_M (local GPU)** under **Local llama.cpp**. Existing processes
may still have an older model registry. Do not switch a large existing cloud
conversation into the 8K local context; start a new conversation instead.
Selecting Qwen now applies the configured **Compact local coding** profile:
`read`, `bash`, `edit`, and `write`, initially with thinking `off`. Web, MCP,
delegation, and context-management tool loaders are not enabled for this
profile. Existing user/project instructions and normal tool-safety hooks are
not removed. Other models and other running workers are not changed.

The profile is personal Pi configuration, not Qwen-specific app code:

- `~/.pi/agent/model-profiles.json`: exact provider/model matches and tool lists.
- `~/.pi/agent/extensions/model-profiles.ts`: auto-discovered extension entry.
- `~/.pi/agent/extensions/model-profiles.mjs`: reusable profile implementation.
- `~/.pi/agent/settings.json`: Qwen-only compaction overrides, with
  `reserveTokens: 2048` and `keepRecentTokens: 2048`; ordinary defaults remain.

New Pi processes pick up the extension automatically. Existing idle processes
need `/reload` in their Termux terminal. Do not reload an active conversation.
The extension restores the prior ordinary tool/thinking baseline when leaving
a profiled model, and before runtime replacement. A deliberate thinking-level
change within the profile is respected on subsequent turns.

The APK does not start Qwen: the localhost server must be running separately.

- OpenAI-compatible API base URL: http://127.0.0.1:8081/v1
- Model ID: qwen3.5-4b
- Pi provider API: openai-completions
- Configured total context: 8192 tokens
- Local server authentication: none (Pi uses the dummy key `local-only`).
- Network binding: 127.0.0.1 only, not exposed to Wi-Fi/LAN.
- Other apps on this same Android device may still be able to access localhost.
- Built-in server agent/shell tools and MCP proxy are not enabled. Tool calls
  are returned as structured API data; Pi executes its own tools.
- Pi's Qwen chat-template compatibility enables/disables thinking correctly;
  `thinking_budget_tokens` is used for explicit reasoning budgets.
- The model is text-only in Pi; image attachments are disabled after selection.

Earlier isolated production-host tests passed discovery, model selection,
arithmetic, real `read` execution and the tool-result reply. That deliberately
lean setup took about 14 seconds for warm arithmetic, 17 seconds for a warm
read/tool-result turn, and 139 seconds for an earlier cold initial prompt.
Those figures do NOT describe the complete everyday Pi configuration.

The real app initially failed on a one-line user question: its installed
extension schemas produced a **14,240-token request**, exceeding the server's
8,192-token limit. The compact profile reduced a fresh normal app-created
worker's actual request to **5,078 tokens**. That worker, with the regular
personal packages/extensions still loaded, successfully returned
`LOCAL_QWEN_READY` with no overflow. Actual server timings were **489.210 s
for prompt evaluation plus 1.204 s generation: about 8 minutes total**.
This fixes context fit, but is not comfortable cold-start latency. Warm
performance depends on prefix reuse and can become slow again when tool or
prompt declarations change. Do not promise the earlier isolated-test timings
for real app sessions. The actual-app report is
`validation-actual-app-compact.json`.

### Full normal-app GPU validation after activation

A fresh production-host-created worker, with ordinary personal packages loaded,
passed all five checks using the final conservative preset:

| Check | Actual elapsed time |
| --- | --- |
| Cold arithmetic, 5,086 input tokens | 76.311 seconds |
| First follow-up, tool-list change forces full prefill | 94.818 seconds |
| Real `read` execution and exact marker reply | 10.664 seconds |
| Cached chat after tool use | 9.723 seconds |
| Second independent real `read` and exact marker reply | 12.352 seconds |

The extra lazy subagent loader can be advertised on the first request by another
installed extension, then removed on the next turn by the compact profile. That
one-time tool-schema change invalidated the prefix cache; do not promise a fast
first follow-up. Loader calls remain blocked by the compact profile.

The newer `stableToolSchemas` profile option filters serialized OpenAI tool
declarations to the configured tools in their configured order. It preserves
their current definitions and all message content. This closes the schema leak
in the profile hook, but does not remove a loader's system-prompt prose or stop a
later provider hook from changing the payload. The diagnostic file now includes
schema/system hashes and change flags. Phone validation of this revision is
pending; the timings above remain historical measurements, not new results.

The final test used a PID/session-scoped diagnostic guard that allowed only its
requested fixture reads and no tools for chat. No call was blocked in the passing
run. The temporary capture/guard extension was removed afterward. Both file reads
were verified from actual Pi tool-call/result entries, not just echoed markers.
The diagnostic worker was closed. A separate blank Qwen GPU app session was
prepared without diagnostic messages, but was found closed during final checks;
it was not automatically recreated. Start a new app session and choose the local
GPU model. This cloud session was not selected away or moved into Qwen. Report: `validation-actual-app-turnip-conservative.json`.

Earlier activation tests exposed repeated answers and a repeated arithmetic-tool
loop; they are retained as failed reports, not hidden. Bounded cache settings,
fusion-off operation, and conservative sampling mitigated these in the final
sequence, but this is not proof of broad/sustained agent reliability or an exact
single-cause diagnosis. Cold/cache-invalidated turns still take about a minute
or longer. This 4B model is not equivalent to a frontier cloud coding model.

Separate Pi RPC thinking mode returned distinct reasoning and the correct
answer with a test-only 128-token reasoning budget (about 65 seconds including
its cold prompt). Earlier reports are `validation-pi-voice-latest-host.json`,
`validation-pi-voice-latest-api.json`, and `validation-pi-reasoning.json`.

Profile behavior has standalone regression tests:
`node --test ~/local-llm/model-profiles.test.mjs`. They cover unmatched models,
exact identities, tool bounds, thinking changes, restoration on switching and
reload, validation, and diagnostic failures.

These are focused integration checks, not a broad correctness/coding benchmark.
Long histories, sustained screen-off behavior, and a real long-running
compaction workload have not been validated with this model.

## Verify and test

Verify the private model's full SHA-256 against pinned Hugging Face metadata:

```sh
~/local-llm/qwen3.5-4b/verify-model.py
```

With the server running, from another Termux session:

```sh
~/local-llm/qwen3.5-4b/smoke-test.py
```

The smoke test checks model listing, a short arithmetic reply, streamed
OpenAI-format read-tool arguments, and a simulated tool-result round trip.
It never executes a tool or shell command and does not contact Pi. Passing
this test is a protocol check, not a broad correctness or coding benchmark.

Validation on 2026-10-25:

- Native Android Download Manager download completed; original retained in Downloads.
- Original and private runtime copy matched the full expected SHA-256.
- CPU / 8K context: model listing, arithmetic reply, streamed structured read-tool
  call, and simulated tool-result round trip all passed (validation-cpu.json).
- CPU thinking / 128-token reasoning budget: correct final answer and separate
  reasoning_content passed (validation-thinking-budgeted.json).
- CPU unbudgeted thinking / 512-token total output cap: exhausted the output cap
  without a correct final answer; logs/validation-thinking.log.
- Vulkan: failed correctness; logs/validation-vulkan.log.
- Vulkan with FP16 disabled and Flash Attention off: also failed;
  validation-vulkan-f32.json and logs/validation-vulkan-f32.log.
- The original preparation test servers were stopped after validation. During
  the later Pi Voice integration check, Qwen was started in its own tmux server
  (`qwen-local`, session `qwen3-5-4b`) and left available at port 8081. No autostart
  is installed.

The isolated Turnip backend is now active and the launcher default, with explicit
CPU fallback available. It passed the later checks described in
GPU-INVESTIGATION.md; the stock-driver Vulkan mode remains incorrect.
This preparation does not imply that Pi's entire prompt, tool collection, long
coding tasks, or sustained GPU operation have been benchmarked.
