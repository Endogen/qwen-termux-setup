# Profile/loader ordering fix — 2026-10-26

**Fixed and phone-validated on Pi 1.4.0 with pi-subagents 0.82.1.**
The first follow-up now reuses the prompt cache instead of evaluating another
roughly 5,000-token prompt. GPU settings, model weights and sampling are unchanged.

## Cause and fix

Pi's resource discovery orders auto-discovered personal extensions before user
package extensions. Previously, `~/.pi/agent/extensions/model-profiles.ts` selected
`read`, `bash`, `edit`, `write`, then pi-subagents' `before_agent_start` handler
re-added `subagents_enable` to both the live tools and structured prompt options.
The later schema filter removed its declaration but not its generated tool-list
line. In full-app tests that line disappeared on the next turn, changing the
system prompt and invalidating the cache.

The installer now places the same profile implementation in a small local package:

- `~/.pi/agent/local-model-profiles/package.json`
- `~/.pi/agent/local-model-profiles/model-profiles.mjs`
- Final entry in `~/.pi/agent/settings.json` → `packages`: `./local-model-profiles`

The profile runs **after the loader hooks**, so its existing structured
`systemPromptOptions.selectedTools` update and `setActiveTools()` call agree before
Pi builds the initial prompt. The auto-discovered legacy `.ts`/`.mjs` pair is
removed with backups and rollback so the profile cannot run twice.

No Pi core, pi-subagents, or Pi Voice code was patched. No prompt string was
scrubbed or frozen. User/project instructions, safety hooks, unrelated packages,
cloud defaults, credentials, and manual thinking-level controls remain intact.
The schema filter remains a defensive guard, but removed **zero tools** in every
post-fix app request observed by the profile hook.

## Phone acceptance results

Two independent fresh normal production-app workers, with the dedicated inference
server restarted before each run. Every arithmetic answer and both actual file
reads passed in each run. Both diagnostic workers were closed afterward; existing
workers and the selected session were retained.

| Check | Run 1 | Run 2 |
| --- | ---: | ---: |
| Cold arithmetic | 65.929 s | 66.257 s |
| First follow-up | **3.410 s** | **3.459 s** |
| Real read and tool-result reply | 9.905 s | 9.256 s |
| Chat after tool use | 11.020 s | 11.031 s |
| Second independently executed read | 10.976 s | 11.019 s |

The first follow-up reused **4,940 cached tokens**, evaluated **82 uncached tokens**,
and returned two output tokens in both runs. All observed requests retained
identical tool-schema and system-prompt hashes. Previously the two strict runs
reused **zero** tokens and took **65.669 / 66.356 s** for this follow-up.
That is roughly a **19× improvement for this specific first-follow-up test**, not
an overall model speedup. Cold startup remains about 66 seconds here. The earlier
synthetic tool-transition failures and broader coding-quality limitations are not
fixed by changing load order.

Sanitized measurements: [loader-order-fix-2026-10-26.json](../evidence/loader-order-fix-2026-10-26.json).
Raw local reports: `validation-loader-order-fixed.json` and
`validation-loader-order-fixed-repeat.json`. They are ignored, not published.
The stricter `QWEN_REQUIRE_WARM_CACHE=1` gate now requires both positive cache reuse
and unchanged system/schema hashes, not merely a correct answer.

## Installation and maintenance

```sh
python scripts/install-pi-config.py                 # review dry-run
python scripts/install-pi-config.py --apply         # migrate with backups
```

Differing extension files require explicit `--replace-extension` after review.
The installer appends/reorders only its own package entry, preserving the order
and options of all other packages. It does not download or update dependencies,
start inference, reload a worker, or change the default model. Start a **fresh
worker process**, or reload an idle worker. Existing processes keep their already
loaded extension order.

**Keep this package last after adding other packages.** Re-running the installer
moves its entry back to the end without altering other entries. This is an ordering
contract for the tested Pi runtime, not an engine-wide tool-policy API; new hooks
or Pi resource-order changes require revalidation. In particular, an SDK host's
inline extension factories may run after packages. Use the tests below in that
host rather than assuming every extension arrangement has this order.

Migration backup on the tested phone:
`~/.pi/agent/.local-qwen-backups/20261026T091330Z-6a50aa2a/`.
For rollback, stop/reload only idle workers and review the backed-up `settings.json`
and legacy extension pair. Restore the prior package list and legacy pair together;
do not retain both entrypoints. Merge any later settings changes rather than
blindly overwriting them. No rollback was needed during validation.

## Regression tests

```sh
npm test
PI_SDK_ROOT=/path/to/@earendil-works/pi-coding-agent npm run test:pi
# With a separately started, idle Qwen server and a running production Pi Voice host:
QWEN_REQUIRE_WARM_CACHE=1 node qwen3.5-4b/verify-actual-app.mjs
```

`npm test` covers migration, exact package ordering, no-op reinstalls, backup and
rollback including file deletions, custom-file conflicts, and the existing profile
behavior tests. It requires neither Pi nor a model.

`test:pi` uses the real Pi SDK, resource discovery and OpenAI serializer with a
local HTTP fixture and temporary configuration. It makes no model/cloud calls and
loads no personal extensions. A minimal loader reproduces the legacy first-request
prompt/schema mismatch (not pi-subagents' full subsequent transcript/pinning state).
The fixed test checks initial/follow-up/tool-result stability, actual fixture-file
execution, legitimate instruction updates, and local → unrestricted → local model
switches. `PI_SDK_ROOT` is unnecessary if the SDK is already resolvable by Node.
Both tests passed against Pi 1.4.0; retain the real-app gate for package interactions.
