# Third-party components and private backup assets

This is a private personal configuration/operational backup. It does not relicense
any upstream code, package, model, or data. Newly authored configuration and scripts
remain private; no blanket open-source license is granted by this repository.

## Packages retained as private release assets

The release archives are unmodified Termux packages downloaded from its configured
APT repository / official packages.termux.dev service. Their SHA-256 values are
locked in `turnip-packages.lock.json`, `runtime-packages.lock.json`, and
`evidence/package-archives.sha256`. Retain the archives and their upstream notices;
do not treat them as new proprietary binaries created by this repository.

| Package | Version | Upstream / terms |
| --- | --- | --- |
| llama-cpp | 0.7.0 | https://github.com/ggml-org/llama.cpp — MIT; components retain their notices |
| llama-cpp-backend-vulkan | 0.7.0 | Same llama.cpp source and component notices |
| mesa-vulkan-icd-freedreno | 26.2.5 | https://gitlab.freedesktop.org/mesa/mesa — Mesa component licenses; see https://docs.mesa3d.org/license.html |
| vulkan-loader-generic | 1.4.366 | https://github.com/KhronosGroup/Vulkan-Loader — Apache-2.0 |
| libdrm | 2.4.134 | https://gitlab.freedesktop.org/mesa/drm — MIT-style notices in the package |
| libxshmfence | 1.3.3-1 | https://gitlab.freedesktop.org/xorg/lib/libxshmfence — MIT-style notices in the package |

Termux build recipes and patches: https://github.com/termux/termux-packages/tree/master/packages
(`llama-cpp`, `mesa-vulkan-icd-freedreno`, `vulkan-loader-generic`, `libdrm`, `libxshmfence`).
This backup is not a complete Termux distribution or an Android system-image backup.
Normal runtime dependencies and the Android environment are separately required.

## Not redistributed

Qwen3.5-4B model weights and the Unsloth GGUF are **not** in Git or the release.
The pinned public source/revision/hash is in `qwen3.5-4b/model-info.json`; the model's
and quantization repository's own licenses/terms apply to downloaded weights.

Pi and the installed personal npm packages remain separately installed upstream
software. `evidence/environment.json` identifies the tested versions. Pi Voice is
also a separate project; the small integration patch retains its upstream file
paths and is intended to be applied only under that project's original terms.
