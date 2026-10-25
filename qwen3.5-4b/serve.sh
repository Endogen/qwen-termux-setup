#!/data/data/com.termux/files/usr/bin/bash
# Foreground, localhost-only inference server. Does not configure Pi or autostart.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODEL="$ROOT/Qwen3.5-4B-Q5_K_M.gguf"
EXPECTED_SIZE=3143656608
if [[ ! -f "$MODEL" || $(stat -c %s "$MODEL") != "$EXPECTED_SIZE" ]]; then
    printf 'Model is missing or incomplete: %s\n' "$MODEL" >&2
    exit 1
fi
if ! command -v llama-server >/dev/null; then
    printf 'Install the runtime first: pkg install llama-cpp\n' >&2
    exit 1
fi
# Validated private Turnip is preferred; CPU remains an explicit fallback.
# Never replace Android's Vulkan libraries or the system ICD, or autostart Pi.
BACKEND="${QWEN_BACKEND:-turnip}"
FLASH_ATTN="${QWEN_FLASH_ATTN:-auto}"
BACKEND_ARGS=()
case "$BACKEND" in
    turnip)
        TURNIP="$ROOT/gpu-driver-experiment"
        TURNIP_LIB="$TURNIP/extracted${PREFIX:?Termux PREFIX is required}/lib"
        if [[ ! -r "$TURNIP/turnip.json" || ! -r "$TURNIP_LIB/libvulkan_freedreno.so" || ! -r "$TURNIP_LIB/libvulkan.so" ]]; then
            printf 'The isolated Turnip driver is missing; see README.md. Use QWEN_BACKEND=cpu for CPU mode.\n' >&2
            exit 1
        fi
        export LD_LIBRARY_PATH="$TURNIP_LIB:${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}$PREFIX/lib"
        export VK_DRIVER_FILES="$TURNIP/turnip.json"
        export VK_ICD_FILENAMES="$TURNIP/turnip.json"
        # Favor correctness over fused kernels on this mobile Vulkan stack.
        export GGML_VK_DISABLE_FUSION=1
        DEVICE=(--device Vulkan0 --n-gpu-layers all --no-op-offload)
        FLASH_ATTN="${QWEN_FLASH_ATTN:-off}"
        # Match isolated validation: no RAM prompt snapshots. Keep only a small
        # number of recurrent context checkpoints on a memory-constrained phone.
        BACKEND_ARGS=(--cache-ram "${QWEN_CACHE_MB:-0}" --ctx-checkpoints "${QWEN_CONTEXT_CHECKPOINTS:-4}")
        ;;
    vulkan)
        if [[ ${QWEN_ALLOW_UNVERIFIED_VULKAN:-0} != 1 ]]; then
            printf 'Stock-driver Vulkan failed correctness tests. Use Turnip (default) or QWEN_BACKEND=cpu.\n' >&2
            printf 'For future debugging only, also set QWEN_ALLOW_UNVERIFIED_VULKAN=1.\n' >&2
            exit 1
        fi
        printf 'WARNING: unverified Vulkan mode; known incorrect output on this device/runtime.\n' >&2
        DEVICE=(--device Vulkan0 --n-gpu-layers all)
        ;;
    cpu) DEVICE=(--device none --n-gpu-layers 0 --no-op-offload) ;;
    *) printf 'QWEN_BACKEND must be cpu, turnip, or vulkan\n' >&2; exit 1 ;;
esac
printf 'Qwen3.5-4B Q5_K_M | backend: %s | context: %s\n' "$BACKEND" "${QWEN_CONTEXT:-8192}"
printf 'API: http://127.0.0.1:%s/v1 | model ID: qwen3.5-4b\n' "${QWEN_PORT:-8081}"
printf 'This does not connect to Pi. Stop with Ctrl+C.\n'
exec llama-server \
    --model "$MODEL" --alias qwen3.5-4b \
    --host 127.0.0.1 --port "${QWEN_PORT:-8081}" \
    --cors-origins localhost --no-agent \
    --ctx-size "${QWEN_CONTEXT:-8192}" --parallel 1 \
    --batch-size 512 --ubatch-size 128 \
    --threads "${QWEN_THREADS:-6}" --threads-batch "${QWEN_THREADS:-6}" \
    "${DEVICE[@]}" --fit off --flash-attn "$FLASH_ATTN" \
    --no-context-shift --jinja \
    --reasoning "${QWEN_REASONING:-auto}" --reasoning-format deepseek \
    --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0 \
    --presence-penalty 1.5 --repeat-penalty 1.0 \
    --n-predict 2048 "${BACKEND_ARGS[@]}" "$@"
