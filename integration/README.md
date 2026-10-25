# Pi Voice dependency boundary

The phone uses `Endogen/pi-voice` at commit:

```
02dae0c432716f71cc4cb2740420afcbc213f6bb
```

`pi-voice-local-registry-refresh.patch` records the additional uncommitted source
changes present during local-Qwen validation:

- Idle, local-only model-registry refresh; busy-worker deferral and concurrency guards.
- Awaited HTTP model-list responses and deferred frontend refresh/recovery.
- Unit/HTTP/real-Pi regressions and documentation.
- Integration fixtures explicitly opt out of real Android wake locks.

These are **generic app fixes**, not Qwen/Turnip-specific policy. They are preserved
here so the exact phone setup is reproducible without silently publishing changes
to the general Pi Voice project. Their working-tree files are left unchanged.
The generated `public/app.js` bundle is deliberately omitted from the patch;
build it from the patched source.

Optional replay on a separate clean checkout:

```sh
git clone https://github.com/Endogen/pi-voice.git ~/pi-voice-restore
cd ~/pi-voice-restore
git checkout 02dae0c432716f71cc4cb2740420afcbc213f6bb
git apply --check ~/local-llm/integration/pi-voice-local-registry-refresh.patch
git apply ~/local-llm/integration/pi-voice-local-registry-refresh.patch
npm ci
npm run build
npm test
```

Do not apply over already-patched files. No script in this repository applies,
commits, pushes, reloads, or rebuilds Pi Voice automatically. Keep real integration
suites sequential, and avoid running them while a user inference request is busy.

The native APK is a separate upstream artifact. The already installed 3.x shell
serves the checkout's assets; changing the inference backend or personal model
profile did not require an APK rebuild. Credentials/signing material and the APK
are not archived here.
