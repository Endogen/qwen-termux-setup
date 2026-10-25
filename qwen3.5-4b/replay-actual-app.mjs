// Reconstruct only our own diagnostic request from its structured Pi log.
// Save no credentials and do not execute proposed tools. Run while Qwen is idle.
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { homedir } from 'node:os';
import assert from 'node:assert/strict';
import { streamSimple } from '/data/data/com.termux/files/home/.pi/agent/install/releases/1.4.0/node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js';

const root = join(homedir(), 'local-llm/qwen3.5-4b');
const report = JSON.parse(await readFile(join(root, 'validation-actual-app-turnip.json'), 'utf8'));
const host = JSON.parse(await readFile(join(homedir(), '.pi/agent/voice/host-state.json'), 'utf8'));
const entry = Object.values(host.entries).find((e) => e.handle === report.handle);
assert.ok(entry?.sessionFile && entry.sessionId === report.sessionId);
const messages = (await readFile(entry.sessionFile, 'utf8')).trim().split('\n').map(JSON.parse)
  .filter((e) => e.type === 'message').map((e) => e.message);
const providers = JSON.parse(await readFile(join(homedir(), '.pi/agent/models.json'), 'utf8')).providers;
const provider = providers['local-llama'];
const declared = provider.models.find((m) => m.id === 'qwen3.5-4b');
const model = { ...declared, provider: 'local-llama', api: provider.api, baseUrl: provider.baseUrl,
  compat: { ...provider.compat, ...declared.compat } };
async function payloadBeforeUser(index) {
  const end = messages.findIndex((m, i) => m.role === 'user' && messages.slice(0, i).filter((p) => p.role === 'user').length === index);
  assert.ok(end >= 0);
  let payload;
  const stream = streamSimple(model, { messages: messages.slice(0, end + 1) }, {
    apiKey: 'local-only', maxTokens: 128, temperature: 0,
    onPayload: (body) => { payload = body; throw new Error('CAPTURE_ONLY_NO_REQUEST'); },
  });
  for await (const _event of stream) { /* stop at capture; no API request */ }
  assert.ok(payload);
  return { ...payload, stream: false, stream_options: undefined, max_tokens: 128, temperature: 0, seed: 1234,
    presence_penalty: 0, repeat_penalty: 1 };
}
const tool = await payloadBeforeUser(2);
const warm = await payloadBeforeUser(1);
await writeFile(join(root, 'logs/actual-app-tool-request.json'), JSON.stringify(tool, null, 2) + '\n', { mode: 0o600 });
await writeFile(join(root, 'logs/actual-app-warm-request.json'), JSON.stringify(warm, null, 2) + '\n', { mode: 0o600 });
const slots = await (await fetch('http://127.0.0.1:8081/slots')).json();
assert.ok(!slots.some((s) => s.is_processing));
const tests = process.argv.includes('--cache-pair')
  ? [['warm-cached', warm, true], ['tool-cached', tool, true]]
  : [['tool-uncached', tool, false]];
for (const [name, body, cache] of tests) {
  const start = Date.now();
  const response = await fetch('http://127.0.0.1:8081/v1/chat/completions', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, cache_prompt: cache }), signal: AbortSignal.timeout(240000),
  });
  const result = await response.json();
  const saved = { name, status: response.status, seconds: (Date.now() - start) / 1000,
    thinkingDisabled: body.chat_template_kwargs, result };
  await writeFile(join(root, `logs/actual-app-replay-${name}${process.env.QWEN_REPLAY_SUFFIX || ''}.json`), JSON.stringify(saved, null, 2) + '\n', { mode: 0o600 });
  console.log(JSON.stringify({ name, seconds: saved.seconds, response: result.choices?.[0], usage: result.usage, timings: result.timings }));
}
