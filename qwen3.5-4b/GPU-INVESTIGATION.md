# Qwen GPU investigation — 2026-10-25

The initial investigation below kept CPU running. The **activation update at the
end supersedes that runtime status**: private Turnip is now active and preferred.

## Finding

The failure is reproducible with llama-cpp **0.7.0**, the downloaded
**Qwen3.5-4B Q5_K_M**, and the phone's **stock Qualcomm Adreno 840 Vulkan
driver**. Model loading succeeds, but generated answers are wrong. CPU inference
and the same Vulkan runtime using **Mesa Turnip 26.2.5** produce correct answers.
This isolates the problem to the llama.cpp/stock-driver interaction rather than
Pi Voice, model registration, a corrupt GGUF, lack of GPU detection, or a missing
GPU capability in principle. We have not isolated the exact failing shader or
proved whether the necessary upstream fix belongs in llama.cpp or Qualcomm's
shader compiler/driver.

Independent Vulkan property queries identify the drivers as:

- Stock: `Qualcomm Technologies Inc. Adreno Vulkan Driver`, Vulkan 1.4.295;
  build `bd918c8d8b`, compiler `E031.50.19.31`, driver date 06/11/26.
- Alternative: `turnip Mesa driver`, `Mesa 26.2.5`, Vulkan 1.4.354.

Raw identities and package hashes are in `gpu-driver-experiment/manifest.json`.

## Controlled correctness matrix

Twenty isolated cases were run with the same model, deterministic temperature
0/seed 1234, and the question “What is 17 multiplied by 23?” expecting exactly
`391`. The matrix uses a 512-token context, four threads, batch 128/microbatch32,
and one short-lived server at a time on localhost port 18082. The separate CPU
server on 8081 was not restarted or used for these requests.

| Configuration | Result |
| --- | --- |
| CPU reference | `391` — pass |
| Stock driver, one GPU layer | `@` — fail |
| Stock driver, two GPU layers | `391  391` — fail |
| Stock driver, full GPU offload | `1@@@@@@@` — fail |
| Stock, one/two layers, fusion disabled | `391` — pass |
| Stock, full GPU offload, fusion disabled | `17 * 23` — fail |
| Stock, full offload, additional FP32/MMVQ/conservative workarounds | fail |
| Turnip, one layer, fusion on/off | `391` — pass in both cases |
| Turnip, full GPU offload, fusion on/off | `391` — pass in both cases |

Disabling fusion mitigates the small partial-offload case but is **not** a
working full-GPU solution with the stock driver. FP16, asynchronous execution,
quantized matrix-vector kernels, and host-visible memory workarounds alone did
not fix its one-layer failure. Changing only the loader/driver to Turnip is the
successful A/B comparison. Full requests, token probabilities, timings, logs,
and exact case settings are in `logs/gpu-investigation/`.

## Stronger Turnip validation

The successful driver was then tested with the normal **8,192-token context**,
six threads, batch 512/microbatch128, full GPU offload, Flash Attention explicitly
**off**, and thinking **off**:

- Direct chat arithmetic: pass, 1.087 seconds.
- Streamed structured `read` tool call: pass, 8.658 seconds.
- Simulated tool-result reply: pass, 2.013 seconds.
- Cold 5,057-token retrieval task: correct exact marker, 65.537 seconds overall;
  prompt evaluation 65.123 seconds, **77.65 prompt tokens/second**;
  six-token output at approximately **12.98 generated tokens/second**.
- Real Pi Voice production host/worker API with an isolated, deliberately lean
  Pi resource configuration: model discovery, selection, history preservation,
  and text-only capability checks passed. Arithmetic took **22.249 seconds**;
  actual Pi `read` execution and its final marker reply took **7.863 seconds**.
  The global settings file remained byte-for-byte unchanged.

These are focused smoke/integration checks, not proof of broad model accuracy or
sustained GPU stability. The long retrieval prompt is comparable in token count,
not identical, to the earlier real-app 5,078-token CPU prompt that took about eight
minutes. Do not present that comparison as a controlled end-to-end application
speedup. A whole normal personal-resource app session has not been benchmarked
on Turnip yet; neither have sustained thermal behavior, screen-off survival,
Flash Attention, or extended thinking.

Reports:

- `validation-turnip.json`
- `validation-turnip-api.json`
- `validation-turnip-pi-voice.json`
- `logs/turnip-validation.log`

Repeat the complete isolated validation while CPU Qwen is idle:

```sh
python ~/local-llm/qwen3.5-4b/validate-turnip.py
```

The validator requires at least 4,200 MiB available memory, refuses an occupied
experimental port, and stops its experimental server afterward.

## Isolation and opt-in use

No root, APK change, Android driver replacement, or global Vulkan package switch
was made. Official Termux APT archives for these versions were **downloaded and
extracted, not installed globally**:

- `vulkan-loader-generic` 1.4.366
- `mesa-vulkan-icd-freedreno` 26.2.5
- `libdrm` 2.4.134
- `libxshmfence` 1.3.3-1

They live under `gpu-driver-experiment/`. Only a Turnip server process receives
the private library search path and `VK_DRIVER_FILES`/`VK_ICD_FILENAMES` values.
Android and the existing CPU server retain their original environments.

`serve.sh` now supports an explicit `turnip` backend. To use it in place of the
CPU server, first stop **only Qwen's existing CPU server** in its own terminal;
then run:

```sh
QWEN_BACKEND=turnip ~/local-llm/qwen3.5-4b/serve.sh
```

This serves the same model ID and localhost endpoint, so Pi's provider config
need not change. Turnip defaults to Flash Attention off to match validation.
After activation, plain `serve.sh` defaults to **Turnip**; use
`QWEN_BACKEND=cpu` for an explicit CPU fallback. The raw `vulkan` backend still
requires its unverified override and should not be used with the stock driver.
At the end of the initial investigation CPU was left running and GPU experiments
were stopped; the following activation update records the subsequent cutover.

## Activation update — normal app, not a lean harness

The user authorized activation. No APK rebuild was needed. The idle CPU runtime
was replaced by Turnip in its existing dedicated `qwen-local` tmux server and
`qwen3-5-4b` session, preserving `127.0.0.1:8081/v1` and the model identity.
An initial restart encountered a port-release race; it safely rolled back to
CPU, then succeeded after a graceful stop and waiting for process/socket release.

Additional real-app testing exposed issues that the initial small checks missed:
a repeated prior answer on a file-read request, and repeated arithmetic-tool calls
after tool use. Fusion-off alone was not sufficient to make every test pass.
The final conservative setup is:

- Turnip 26.2.5, full 33/33 GPU layer offload, 8,192-token context.
- Flash Attention off and `GGML_VK_DISABLE_FUSION=1`.
- `--cache-ram 0` to avoid the default 8 GiB CPU prompt-snapshot pool.
- `--ctx-checkpoints 4` to bound recurrent checkpoint memory.
- Pi thinking-off sampling: temperature 0.2, top_p 0.8, presence penalty 0.
- Existing four-tool compact profile and thinking-off policy retained.

A fresh **normal production app-created worker with the full personal resources**
then passed this complete sequence:

| Test | End-to-end elapsed |
| --- | --- |
| Cold arithmetic, 5,086 input tokens | 76.311 seconds |
| First follow-up arithmetic | 94.818 seconds |
| Actual Pi file read and exact marker result | 10.664 seconds |
| Cached arithmetic after that tool result | 9.723 seconds |
| Second independent file read and exact marker result | 12.352 seconds |

A separate extension re-advertises a lazy loader on the first turn; the compact
profile removes it on the next. The resulting schema change caused a complete
prefill on that first follow-up. Later turns reused thousands of cached tokens.
Cold/cache-invalidated turns remain slow; these numbers do not establish sustained
thermal stability or broad coding-agent accuracy.

A temporary diagnostic extension captured only the explicitly authorized test
PID/session, no headers or credentials. It also constrained tool execution to the
test's requested fixture reads. **No tool call was blocked in the passing run.**
Both reads were verified against actual tool-call/result records. That extension
was removed afterward, and the completed diagnostic worker was closed. Older
failed reports are retained. Because multiple settings changed, no single shader,
cache operation, or sampling parameter has been proven to be the sole cause of
those failures. The final sequence is a useful functional check, not a guarantee
against small-model mistakes or future tool loops.

`serve.sh` now prefers Turnip for subsequent manual starts; there is still no
boot service, automatic cloud fallback, or APK-managed inference startup. The
provider display name is **Qwen3.5 4B Q5_K_M (local GPU)**. Only that local model's
display name and thinking-off sampling were changed; cloud defaults were not.
A separate fresh blank local GPU worker was prepared without test messages;
it was found closed during final checks and was not automatically recreated.
The existing cloud conversation remained selected. Create a fresh app session
and select the local GPU model to use the active server.

Final evidence:

- `validation-actual-app-turnip-conservative.json` — full five-check PASS.
- `runtime-turnip-activation.json` — active runtime and clean ready-session metadata.
- `logs/turnip-production-server.log` — actual server/offload/prefix-cache timings.
