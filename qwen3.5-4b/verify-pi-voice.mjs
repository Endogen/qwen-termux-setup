// Real local inference through Pi Voice's production host, worker, Pi registry
// and Pi tool execution. Isolated state; never changes a user's active session.
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, mkdir, rm, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { homedir, tmpdir } from 'node:os';
import { randomUUID, createHash } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { createHost } from '/data/data/com.termux/files/home/pi-voice/host/server.mjs';
import { createLauncher } from '/data/data/com.termux/files/home/pi-voice/host/launcher.mjs';
import { configureTestModels } from '/data/data/com.termux/files/home/pi-voice/test/fixtures/test-models.mjs';

const exec = promisify(execFile);
const home = homedir();
const root = await mkdtemp(join(tmpdir(), 'pi-voice-qwen-'));
const voice = join(root, 'voice');
const socket = `qwen-check-${randomUUID().slice(0, 8)}`;
const tmux = `${process.env.PREFIX}/bin/tmux`;
const settings = await readFile(join(home, '.pi/agent/settings.json'));
const started = Date.now();
const report = { started: new Date().toISOString(), backend: process.env.QWEN_TEST_BACKEND || 'CPU', model: 'local-llama/qwen3.5-4b', tests: {} };
let host;
let token;
async function request(path, body, entry) {
  const result = await fetch(`${host.origin}/api/${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json',
      ...(entry ? { 'X-Pi-Voice-Session': entry.handle, 'X-Pi-Voice-Instance': entry.instanceId } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(30000),
  });
  const data = await result.json();
  assert.ok(result.ok, `${path}: ${result.status}: ${data.error || ''}`);
  return data;
}
async function until(check, label, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const result = await check(); if (result) return result; await delay(500); }
  throw new Error(`Timed out: ${label}`);
}
try {
  await configureTestModels(root);
  const isolated = JSON.parse(await readFile(join(root, 'models.json'), 'utf8'));
  const configured = JSON.parse(await readFile(join(home, '.pi/agent/models.json'), 'utf8'));
  Object.assign(isolated.providers, configured.providers);
  if (process.env.QWEN_API_URL) isolated.providers['local-llama'].baseUrl = process.env.QWEN_API_URL;
  await writeFile(join(root, 'models.json'), JSON.stringify(isolated), { mode: 0o600 });
  await mkdir(voice, { recursive: true });
  await writeFile(join(voice, 'wake-settings.json'), JSON.stringify({ enabled: false }), { mode: 0o600 });
  const launcher = createLauncher({ socket, tmux, env: { ...process.env, PI_CODING_AGENT_DIR: root, TERM: 'xterm-256color' },
    args: ['--offline', '--no-context-files', '--no-skills', '--no-prompt-templates', '--no-themes', '--no-extensions',
      '--provider', 'pi-voice-test', '--model', 'vision', '--thinking', 'off', '-e', `${home}/pi-voice/extension.mjs`] });
  host = await createHost({ directory: voice, port: 0, interval: 100, launcher, managerOptions: { cwd: root } });
  await host.ready;
  token = (await readFile(join(voice, 'token'), 'utf8')).trim();
  const worker = await until(async () => (await request('sessions')).sessions.find((item) => item.status === 'ready'), 'fresh Pi worker');
  const before = await request('state', undefined, worker);
  assert.equal(before.wakeLock.active, false, 'Verification must not acquire an Android wake lock');
  const models = await request('models', undefined, worker);
  const local = models.models.find((model) => model.provider === 'local-llama' && model.id === 'qwen3.5-4b');
  assert.ok(local, 'The app must discover the registered local model');
  assert.deepEqual(local.input, ['text']);
  assert.equal(local.contextWindow, 8192);
  const selected = await request('model', { sessionId: worker.sessionId, provider: local.provider, id: local.id, expectedModel: models.current }, worker);
  const after = await request('state', undefined, worker);
  assert.deepEqual(after.modelSelection, { provider: local.provider, id: local.id });
  assert.equal(after.modelSupportsImages, false);
  assert.deepEqual(after.messages, before.messages);
  report.tests.model_listing_selection_capabilities = 'PASS';
  console.log('PASS: fresh Pi Voice worker lists and selects local Qwen, preserves history and reports text-only capability.');

  const arithmeticStart = Date.now();
  await request('prompt', { id: randomUUID(), sessionId: worker.sessionId, text: 'What is 17 multiplied by 23? Reply only with the integer. Do not use any tools.' }, worker);
  const arithmetic = await until(async () => {
    const state = await request('state', undefined, worker);
    return !state.busy && state.messages.some((message) => message.role === 'assistant') ? state : null;
  }, 'actual local arithmetic reply', 600000);
  const arithmeticReply = arithmetic.messages.filter((message) => message.role === 'assistant').at(-1);
  assert.equal(arithmeticReply.text.trim(), '391', JSON.stringify(arithmeticReply));
  report.tests.real_chat = { status: 'PASS', seconds: (Date.now() - arithmeticStart) / 1000, reply: arithmeticReply.text };
  console.log(`PASS: actual Qwen reply via app API: ${arithmeticReply.text} (${report.tests.real_chat.seconds}s)`);

  const marker = `QWEN_PI_VOICE_VERIFIED_${randomUUID().slice(0, 8)}`;
  const fixture = join(root, 'verification.txt');
  await writeFile(fixture, marker + '\n');
  const toolStart = Date.now();
  await request('prompt', { id: randomUUID(), sessionId: worker.sessionId, text: `Use the read tool to read ${fixture}, then reply with exactly the file contents. Do not use any other tool.` }, worker);
  const toolState = await until(async () => {
    const state = await request('state', undefined, worker);
    return !state.busy && state.messages.filter((message) => message.role === 'assistant').length > arithmetic.messages.filter((message) => message.role === 'assistant').length ? state : null;
  }, 'actual Pi read tool and local final reply', 600000);
  const toolReply = toolState.messages.filter((message) => message.role === 'assistant').at(-1);
  assert.ok(toolReply.text.includes(marker), JSON.stringify(toolReply));
  const panes = await exec(tmux, ['-L', socket, 'list-panes', '-a', '-F', '#{pane_id}']);
  const pane = panes.stdout.trim().split('\n')[0];
  assert.ok(pane, 'The isolated Pi terminal must still exist');
  const files = await exec(tmux, ['-L', socket, 'capture-pane', '-p', '-t', pane, '-S', '-200']);
  assert.ok(files.stdout.includes('read') && files.stdout.includes('verification.txt'), 'Real Pi must execute the read tool, not invent file contents');
  report.tests.real_read_tool_and_result = { status: 'PASS', seconds: (Date.now() - toolStart) / 1000, reply: toolReply.text };
  assert.deepEqual(await readFile(join(home, '.pi/agent/settings.json')), settings, 'User defaults must remain unchanged');
  report.settings_sha256_unchanged = createHash('sha256').update(settings).digest('hex');
  report.status = 'PASS';
  console.log(`PASS: actual Pi read tool and Qwen tool-result reply (${report.tests.real_read_tool_and_result.seconds}s)`);
} catch (error) {
  report.status = 'FAIL'; report.error = String(error.stack || error);
  console.error(report.error);
  process.exitCode = 1;
} finally {
  report.seconds = (Date.now() - started) / 1000;
  await writeFile(process.env.QWEN_TEST_REPORT || join(home, 'local-llm/qwen3.5-4b/validation-pi-voice-latest-host.json'), JSON.stringify(report, null, 2) + '\n');
  await host?.close();
  await exec(tmux, ['-L', socket, 'kill-server']).catch(() => {});
  await rm(root, { recursive: true, force: true });
}
