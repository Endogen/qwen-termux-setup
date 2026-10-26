// Full personal configuration through the running production Pi Voice host.
// Creates and targets only its own fresh worker, never selects it in the UI,
// and closes that worker after completion. Existing conversations are untouched.
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdtemp, rm } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID, createHash } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';

const root = join(homedir(), 'local-llm/qwen3.5-4b');
const voice = join(homedir(), '.pi/agent/voice');
const { origin } = JSON.parse(await readFile(join(voice, 'host-endpoint.json'), 'utf8'));
const token = (await readFile(join(voice, 'token'), 'utf8')).trim();
const fixtureDir = await mkdtemp(join(tmpdir(), 'qwen-turnip-actual-'));
const report = { started: new Date().toISOString(), backend: 'Mesa Turnip / Vulkan',
  verificationScope: 'fresh normal production app-created worker with full personal resources and compact model profile', tests: {} };
const settingsPath = join(homedir(), '.pi/agent/settings.json');
const settingsBefore = await readFile(settingsPath);
let worker;
let initial;
let completed = false;
async function api(path, body, target) {
  const response = await fetch(`${origin}/api/${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json',
      ...(target ? { 'X-Pi-Voice-Session': target.handle, 'X-Pi-Voice-Instance': target.instanceId } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(30000),
  });
  const result = await response.json();
  assert.ok(response.ok, `${path}: HTTP ${response.status}: ${result.error || ''}`);
  return result;
}
async function until(check, label, timeout = 60000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const result = await check();
    if (result) return result;
    await delay(750);
  }
  throw new Error(`Timed out: ${label}`);
}
async function entries() {
  const metadata = JSON.parse(await readFile(join(voice, 'running', `${worker.pid}.json`), 'utf8'));
  assert.equal(metadata.instanceId, worker.instanceId);
  assert.equal(metadata.sessionId, worker.sessionId);
  return (await readFile(metadata.sessionFile, 'utf8')).trim().split('\n').map(JSON.parse);
}
async function turn(label, text, expected) {
  if (process.env.QWEN_CAPTURE === '1') await writeFile(join(root, 'logs/capture-allow.json'), JSON.stringify({ pid: worker.pid, sessionId: worker.sessionId,
    allowedTools: label.includes('read_tool') ? ['read'] : [], fixtureDirectory: fixtureDir }), { mode: 0o600 });
  const before = await api('state', undefined, worker);
  assert.ok(!before.busy && !before.pending);
  const count = before.messages.filter((m) => m.role === 'assistant').length;
  const started = Date.now();
  await api('prompt', { id: randomUUID(), sessionId: worker.sessionId, text }, worker);
  const after = await until(async () => {
    const state = await api('state', undefined, worker);
    assert.equal(state.sessionId, worker.sessionId, 'Diagnostic worker changed identity');
    return !state.busy && !state.pending && state.messages.filter((m) => m.role === 'assistant').length > count ? state : null;
  }, label, 360000);
  const reply = after.messages.filter((m) => m.role === 'assistant').at(-1);
  assert.equal(reply.text.trim(), expected, `${label}: ${JSON.stringify(reply)}`);
  const messages = (await entries()).filter((e) => e.type === 'message').map((e) => e.message);
  const assistant = messages.filter((m) => m.role === 'assistant').at(-1);
  report.tests[label] = { status: 'PASS', seconds: (Date.now() - started) / 1000,
    reply: reply.text, usage: assistant?.usage };
  // Hashes only: no prompt contents. This is the profile hook's observation;
  // another extension later in provider-hook order can still rewrite payloads.
  const profileState = JSON.parse(await readFile(join(voice, `model-profile-${worker.pid}.json`), 'utf8'));
  report.tests[label].profileRequest = profileState.request ?? null;
  if (label === 'warm_chat' && process.env.QWEN_REQUIRE_WARM_CACHE === '1') {
    assert.ok(assistant?.usage?.cacheRead > 0,
      'First follow-up did not reuse cached input; inspect prompt/schema hashes and server checkpoints');
  }
  console.log(`PASS ${label}: ${JSON.stringify(report.tests[label])}`);
  return messages;
}
try {
  initial = await api('sessions');
  assert.ok(initial.sessions.length < 4, 'No spare app worker capacity');
  const existingQwen = initial.sessions.find((e) => e.status === 'ready' && e.model === 'local-llama/qwen3.5-4b');
  assert.ok(!existingQwen || (!existingQwen.busy && !existingQwen.pending), 'Existing Qwen must be idle');
  const source = existingQwen || initial.sessions.find((e) => e.status === 'ready');
  assert.ok(source, 'A source working directory is required');
  const sourceState = existingQwen && await api('state', undefined, existingQwen);
  const historyHash = sourceState && createHash('sha256').update(JSON.stringify(sourceState.messages)).digest('hex');
  const spawned = await api('session/spawn', { id: randomUUID() }, source);
  worker = await until(async () => (await api('sessions')).sessions.find((e) => e.handle === spawned.handle && e.status === 'ready'), 'normal worker startup');
  report.handle = worker.handle;
  report.pid = worker.pid;
  report.sessionId = worker.sessionId;
  if (process.env.QWEN_CAPTURE === '1') await writeFile(join(root, 'logs/capture-allow.json'), JSON.stringify({ pid: worker.pid, sessionId: worker.sessionId }), { mode: 0o600 });
  const models = await api('models', undefined, worker);
  const local = models.models.find((m) => m.provider === 'local-llama' && m.id === 'qwen3.5-4b');
  assert.ok(local);
  await api('model', { sessionId: worker.sessionId, provider: local.provider, id: local.id, expectedModel: models.current }, worker);
  const profile = JSON.parse(await readFile(join(voice, `model-profile-${worker.pid}.json`), 'utf8'));
  assert.deepEqual(profile.tools, ['read', 'bash', 'edit', 'write']);
  assert.equal(profile.thinking, 'off');
  report.profile = profile;
  await turn('cold_chat', 'What is 17 multiplied by 23? Reply only with the integer. Do not use any tools.', '391');
  await turn('warm_chat', 'What is 2 plus 2? Reply only with the integer. Do not use any tools.', '4');
  const fixture = join(fixtureDir, 'verification.txt');
  const marker = `TURNIP_ACTUAL_APP_${randomUUID().slice(0, 8)}`;
  await writeFile(fixture, marker + '\n', { mode: 0o600 });
  const messages = await turn('real_read_tool_and_result', `Use the read tool to read ${fixture}. Reply only with the exact file contents and nothing else. Do not use other tools.`, marker);
  const call = messages.flatMap((m) => m.role === 'assistant' && Array.isArray(m.content) ? m.content : [])
    .find((c) => c.type === 'toolCall' && c.name === 'read' && c.arguments?.path === fixture);
  assert.ok(call, 'Read tool call must exist in the actual Pi session log');
  const result = messages.find((m) => m.role === 'toolResult' && m.toolCallId === call.id);
  assert.ok(result && !result.isError && JSON.stringify(result.content).includes(marker), 'Pi must really read the fixture, not invent a marker');
  report.tests.real_read_tool_and_result.realExecutionVerified = true;
  await turn('steady_warm_chat', 'What is 3 multiplied by 3? Reply only with the integer. Do not use any tools.', '9');
  const secondFixture = join(fixtureDir, 'verification-second.txt');
  const secondMarker = `TURNIP_SECOND_READ_${randomUUID().slice(0, 8)}`;
  await writeFile(secondFixture, secondMarker + '\n', { mode: 0o600 });
  const secondMessages = await turn('second_real_read_tool_and_result', `Use the read tool to read ${secondFixture}. Reply only with the exact file contents and nothing else. Do not use other tools.`, secondMarker);
  const secondCall = secondMessages.flatMap((m) => m.role === 'assistant' && Array.isArray(m.content) ? m.content : [])
    .find((c) => c.type === 'toolCall' && c.name === 'read' && c.arguments?.path === secondFixture);
  assert.ok(secondCall, 'Second real file read must be independently executed');
  assert.ok(secondMessages.some((m) => m.role === 'toolResult' && m.toolCallId === secondCall.id && !m.isError && JSON.stringify(m.content).includes(secondMarker)));
  report.tests.second_real_read_tool_and_result.realExecutionVerified = true;
  if (existingQwen) {
    const originalNow = await api('state', undefined, existingQwen);
    report.originalQwenHistoryUnchanged = historyHash === createHash('sha256').update(JSON.stringify(originalNow.messages)).digest('hex');
    assert.ok(report.originalQwenHistoryUnchanged);
  } else report.originalQwenHistoryUnchanged = 'No existing live Qwen session; only the new diagnostic worker was used';
  const final = await api('sessions');
  report.selectedSessionUnchanged = final.selected === initial.selected;
  report.existingWorkersRetained = initial.sessions.every((e) => final.sessions.some((f) => f.handle === e.handle && f.instanceId === e.instanceId));
  assert.ok(report.existingWorkersRetained);
  assert.deepEqual(await readFile(settingsPath), settingsBefore);
  report.globalSettingsUnchanged = true;
  report.status = 'PASS';
  completed = true;
} catch (error) {
  report.status = 'FAIL'; report.error = String(error.stack || error);
  console.error(report.error); process.exitCode = 1;
} finally {
  if (worker) {
    try {
      const state = await api('state', undefined, worker);
      const snapshot = await api('sessions');
      if (!state.busy && !state.pending && snapshot.selected !== worker.handle && state.sessionId === worker.sessionId) {
        await api('session/close', { instanceId: worker.instanceId, sessionId: worker.sessionId }, worker);
        await until(async () => !(await api('sessions')).sessions.some((e) => e.handle === worker.handle), 'diagnostic worker shutdown');
        report.diagnosticWorkerClosed = true;
      } else report.diagnosticWorkerClosed = false;
    } catch (error) { report.cleanupError = String(error); }
  }
  if (completed || report.diagnosticWorkerClosed) await rm(fixtureDir, { recursive: true, force: true });
  if (process.env.QWEN_CAPTURE === '1') await rm(join(root, 'logs/capture-allow.json'), { force: true });
  const output = process.env.QWEN_TEST_REPORT || join(root, 'validation-actual-app-turnip.json');
  await writeFile(output, JSON.stringify(report, null, 2) + '\n');
  console.log(`Report: ${output}`);
}
