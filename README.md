# Qwen3.5 on OnePlus / Termux — private setup

Phone-specific runtime and Pi configuration, deliberately **separate from Pi Voice**.
This repository is the working `~/local-llm` directory on the configured phone.
It does not change the general app into a Qwen-specific app.

## Current validated setup

- OnePlus 15 / CPH2747, SM8850, Adreno 840, Android 16, approximately 16 GiB RAM.
- Qwen3.5-4B **Q5_K_M**, text only; SHA-256 and pinned download URL in
  [`model-info.json`](qwen3.5-4b/model-info.json).
- Termux llama-cpp **0.7.0** and its Vulkan backend.
- **Private Mesa Turnip 26.2.5** with private Vulkan loader 1.4.366. The stock
  Qualcomm driver generates wrong answers in the tested Vulkan path.
- Full 33/33 layer GPU offload; 8,192-token context, one slot, six CPU threads;
  fusion and Flash Attention off; RAM prompt snapshots off, four recurrent checkpoints.
- Pi **1.4.0**, optional `local-llama/qwen3.5-4b`; thinking initially off;
  `read`, `bash`, `edit`, `write`; temperature 0.2, top_p 0.8, presence penalty 0.
- Qwen-only compaction reserve/recent budgets of 2,048 tokens each.
- API `http://127.0.0.1:8081/v1`; model ID `qwen3.5-4b`.

The final full-personal-resource app sequence passed chat, two actual file reads,
and chat after tool use. Cold/cache-invalidated turns took **76–95 seconds**;
later cached turns took **10–12 seconds**. A tool-list transition can invalidate
that first follow-up's cache. This is a focused functional check, **not** proof of
broad coding accuracy, sustained thermal stability, or frontier-model quality.
Earlier failures and their mitigation are recorded, not hidden:

- [Runtime instructions and limitations](qwen3.5-4b/README.md)
- [GPU investigation and activation](qwen3.5-4b/GPU-INVESTIGATION.md)
- [Sanitized validation summary](evidence/validation-summary.json)
- [Exact environment baseline](evidence/environment.json)
- [GPU latency tuning and cache validation](qwen3.5-4b/PERFORMANCE.md)

Local optimization work (not yet phone-validated): the compact profile now
filters late-added OpenAI tool schemas before sending and records schema/system
fingerprints. The launcher exposes batch/ubatch and independent prefill thread
controls, with the validated defaults retained. A sequential GPU/cache benchmark
is included. No new speedup is claimed until measured on the OnePlus.

## What is backed up

Runtime/verification scripts; the generic personal model-profile extension and
its exact-match configuration; **local-only** provider/settings templates; safe
configuration merge tooling; package versions/hashes; diagnostic source code;
sanitized success/failure evidence; and an optional Pi Voice dependency patch.

The private `v0.1.0` GitHub release preserves the six unmodified, hash-pinned
Termux archives: four private-driver dependencies and the two inference packages.
They are release assets, not Git source blobs. See [third-party notices](THIRD_PARTY.md).

**Not uploaded:** the 3.14 GB GGUF, credentials, device ID, cloud defaults,
conversation files, raw prompts/SSE captures, active-session metadata, APK/signing
material, extracted shared libraries, configuration backups, or raw logs.
Weights remain on the phone and are recoverable from the pinned Hugging Face URL.
The `local-only` API key in `pi/models.local.json` is a dummy for an unauthenticated
localhost server, not a real credential.

## Already configured phone

No reinstall/rebuild is required. Do not clone over or replace this directory.
The Qwen runtime is independent of the APK and runs in its dedicated `qwen-local`
tmux server. Publishing this repository does not reload any worker or restart it.

```sh
~/local-llm/qwen3.5-4b/serve.sh  # foreground manual start, when port 8081 is free
```

For background manual operation:

```sh
tmux -L qwen-local new-session -d -s qwen3-5-4b \
  'exec ~/local-llm/qwen3.5-4b/serve.sh'
```

Do not start another copy if it is already running. Before changing a backend,
finish requests, stop **only Qwen's server**, and wait for its process/socket to
exit. `QWEN_BACKEND=cpu` is the explicit fallback; no silent backend/cloud fallback
or boot autostart exists.

## Restore on a fresh compatible phone / Termux

These are **restoration instructions**, not commands needed on the current phone.
The driver archives require the standard `com.termux` aarch64 prefix. Other GPUs,
Android drivers, Pi versions, or quantizations require fresh correctness testing.

1. Install Termux prerequisites and Pi/Pi Voice using their own instructions:
   `git`, `gh`, Python, Node.js >=22, tmux, `libandroid-spawn`, `libc++`, `libcurl`.
   Authenticate `gh` yourself; no GitHub or cloud credentials are stored here.
2. Clone this private repository to `~/local-llm`:

   ```sh
   gh repo clone Endogen/qwen-termux-setup ~/local-llm
   cd ~/local-llm
   ```

3. Download private package assets, then verify **all six** SHA-256 values:

   ```sh
   mkdir -p qwen3.5-4b/gpu-driver-experiment/debs
   gh release download v0.1.0 --repo Endogen/qwen-termux-setup \
     --pattern '*.deb' --dir qwen3.5-4b/gpu-driver-experiment/debs
   (cd qwen3.5-4b/gpu-driver-experiment/debs && \
     sha256sum --check "$HOME/local-llm/evidence/package-archives.sha256")
   ```

4. Install **only the two inference packages**, resolving normal Termux dependencies:

   ```sh
   cd ~/local-llm/qwen3.5-4b/gpu-driver-experiment/debs
   apt install ./llama-cpp_0.7.0_aarch64.deb ./llama-cpp-backend-vulkan_0.7.0_aarch64.deb
   cd ~/local-llm
   python scripts/prepare-turnip.py --apply
   ```

   **Do not install every `.deb` with a wildcard.** The generic Vulkan loader and
   Turnip packages are extracted **privately**, not installed over Android's loader.
   `prepare-turnip.py` verifies hashes and refuses extraction while port 8081 is active.
   If release assets are unavailable, `--download` can fetch the exact locked driver
   versions from configured Termux APT, but old versions may leave rolling mirrors.

5. Use Android's standard Download Manager to fetch the pinned public URL in
   `qwen3.5-4b/model-info.json`. After granting Termux shared-storage access, copy
   the completed GGUF from `/storage/emulated/0/Download/` to `qwen3.5-4b/` and run:

   ```sh
   python qwen3.5-4b/verify-model.py
   ```

6. Review `pi/`, then plan/apply the merge without replacing cloud defaults:

   ```sh
   python scripts/install-pi-config.py             # dry-run by default
   python scripts/install-pi-config.py --apply     # backups; local-only merge
   ```

   Existing differing profile extension files require `--replace-extension` after
   review. Conflicting provider/profile ownership is rejected. This tool never
   copies `auth.json`, rewrites cloud defaults, installs packages, reloads a worker,
   or starts inference. Use a fresh Pi worker, or reload only an idle one afterward.
   A custom agent directory is supported with `--agent-dir` / `PI_CODING_AGENT_DIR`.
7. Start `serve.sh` manually, create a **fresh** app session, then choose
   **Qwen3.5 4B Q5_K_M (local GPU)**. Do not move a long cloud conversation into 8K.

## Pi Voice boundary

Pi Voice remains a separate general-purpose project. No model file, driver,
provider policy, or profile installer is added to it by this repository.
The exact local generic registry-refresh/test changes are archived as an optional
[patch with its baseline](integration/README.md); this repository does not apply
or push that patch automatically. The existing APK does not need rebuilding for
runtime/provider/profile changes.

## Tests and diagnostics

```sh
npm test  # portable profile, launcher, benchmark-parser, merge/rollback, archive-safety checks
```

No model weights, GPU, Pi installation, cloud credits, or network are required for
those unit tests. CI runs them on Linux; it is not an Android GPU benchmark.

Inference checks are **manual and opt-in**: `smoke-test.py`, `validate-turnip.py`,
`verify-pi-voice.mjs`, and `verify-actual-app.mjs`. They consume time/memory and some
create temporary app workers. Only run while the real Qwen session is idle; never
reload/abort another user's work to make a test run. The lean isolated checks do
not stand in for the full personal-resource app check.

Raw reports and forensic captures referenced in the detailed historical notes
remain local/ignored; only distilled results are committed. `capture-diagnostic.ts`
is source, **not installed automatically**. It can record complete prompts and
must only be loaded with an explicit PID/session-scoped permit for your own test.
