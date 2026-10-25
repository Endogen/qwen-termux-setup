import test from 'node:test';
import assert from 'node:assert/strict';
import { installModelProfiles, validateProfiles } from './pi/extensions/model-profiles.mjs';

const full = ['read', 'bash', 'edit', 'write', 'web_search', 'mcp'];
const core = ['read', 'bash', 'edit', 'write'];
const local = { provider: 'custom-local', id: 'a/model:Q5' };
const cloud = { provider: 'cloud', id: 'large' };
const config = () => ({ profiles: [{ name: 'Compact local', models: [local], tools: core, thinking: 'off' }] });
function fixture(model = cloud, settings = config()) {
  const events = new Map(); const calls = []; const reports = [];
  let tools = [...full]; let thinking = 'xhigh';
  const pi = {
    on: (name, callback) => events.set(name, callback),
    getActiveTools: () => [...tools], getAllTools: () => full.map((name) => ({ name })),
    setActiveTools: (value) => { calls.push(['tools', value]); tools = [...value]; },
    getThinkingLevel: () => thinking,
    setThinkingLevel: (value) => { calls.push(['thinking', value]); thinking = value; },
  };
  const ctx = { model, ui: { setStatus() {} }, getSystemPrompt: () => 'Preserved user instructions' };
  installModelProfiles(pi, settings, (state) => reports.push(state));
  return { pi, ctx, calls, reports, events, run: (name, event = {}) => events.get(name)(event, ctx) };
}

test('unlisted models retain their complete tools and thinking settings', () => {
  const h = fixture(); h.run('session_start'); h.run('before_agent_start');
  assert.deepEqual(h.calls, []); assert.deepEqual(h.reports, []);
  assert.deepEqual(h.pi.getActiveTools(), full); assert.equal(h.pi.getThinkingLevel(), 'xhigh');
});

test('exact provider/id profiles support slash-containing IDs and do not replace instructions', () => {
  const h = fixture(local); const event = { systemPromptOptions: { selectedTools: full, sections: { safety: 'Keep this' } } };
  h.run('session_start'); h.run('before_agent_start', event);
  assert.deepEqual(h.pi.getActiveTools(), core); assert.equal(h.pi.getThinkingLevel(), 'off');
  assert.deepEqual(event.systemPromptOptions.selectedTools, core);
  assert.deepEqual(event.systemPromptOptions.sections, { safety: 'Keep this' });
  assert.equal(h.reports.at(-1).profile, 'Compact local');
  const other = fixture({ ...local, provider: 'different-provider' }); other.run('session_start');
  assert.deepEqual(other.calls, []);
});

test('switching back restores the previous non-profile tool and thinking baseline', () => {
  const h = fixture(); h.run('session_start');
  h.ctx.model = local; h.run('model_select');
  assert.deepEqual(h.pi.getActiveTools(), core);
  const event = { systemPromptOptions: { selectedTools: core } };
  h.ctx.model = cloud; h.run('before_agent_start', event);
  assert.deepEqual(h.pi.getActiveTools(), full); assert.equal(h.pi.getThinkingLevel(), 'xhigh');
  assert.deepEqual(event.systemPromptOptions.selectedTools, full);
});

test('each new turn bounds the tool set but respects thinking manually changed within a profile', () => {
  const h = fixture(local); h.run('session_start');
  h.pi.setThinkingLevel('low'); h.pi.setActiveTools(full);
  h.run('before_agent_start', { systemPromptOptions: {} });
  assert.deepEqual(h.pi.getActiveTools(), core); assert.equal(h.pi.getThinkingLevel(), 'low');
});

test('shutdown restores normal controls so reload and session replacement do not inherit compact defaults', () => {
  const h = fixture(local); h.run('session_start'); h.run('session_shutdown');
  assert.deepEqual(h.pi.getActiveTools(), full); assert.equal(h.pi.getThinkingLevel(), 'xhigh');
  h.ctx.model = cloud; h.run('session_start');
  assert.deepEqual(h.pi.getActiveTools(), full);
});

test('invalid, duplicate or ambiguous profiles are rejected rather than silently applying partial controls', () => {
  for (const value of [null, {}, { profiles: [null] }, { profiles: [{ ...config().profiles[0], thinking: 'unknown' }] },
    { profiles: [{ ...config().profiles[0], tools: ['read', 'read'] }] },
    { profiles: [config().profiles[0], config().profiles[0]] }]) {
    assert.throws(() => validateProfiles(value));
  }
  assert.deepEqual(validateProfiles({ profiles: [] }), []);
  const h = fixture(local, { profiles: [{ ...config().profiles[0], tools: ['not-installed'] }] });
  assert.throws(() => h.run('session_start'), /unavailable tools/);
  assert.deepEqual(h.calls, []);
});

test('compact profiles allow their tools but block loaders added by other extensions from expanding context mid-run', () => {
  const h = fixture(local); h.run('session_start');
  assert.equal(h.run('tool_call', { toolName: 'read' }), undefined);
  assert.equal(h.run('tool_call', { toolName: 'subagents_enable' }).block, true);
  assert.equal(h.run('tool_call', { toolName: 'mcp' }).block, true);
  h.ctx.model = cloud;
  assert.equal(h.run('tool_call', { toolName: 'mcp' }), undefined);
});

test('diagnostic failures cannot interrupt a turn', () => {
  const h = fixture();
  installModelProfiles(h.pi, config(), () => { throw new Error('Read-only diagnostics directory'); });
  h.ctx.model = local;
  assert.doesNotThrow(() => h.run('session_start'));
  assert.deepEqual(h.pi.getActiveTools(), core);
});
