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

const schema = (name) => ({ type: 'function', function: { name, description: `Use ${name}`,
  parameters: { type: 'object', properties: { path: { type: 'string' } } } } });
function stableFixture() {
  return fixture({ ...local, api: 'openai-completions' }, {
    profiles: [{ ...config().profiles[0], stableToolSchemas: true }],
  });
}

test('late loader activation cannot alter outgoing schemas or their configured order', () => {
  const h = stableFixture(); h.run('session_start'); h.run('before_agent_start');
  // Simulate an extension running later in before_agent_start.
  h.pi.setActiveTools([...core, 'subagents_enable']);
  const payload = { tools: ['subagents_enable', ...core.toReversed()].map(schema),
    messages: [{ role: 'system', content: 'Keep all user instructions' }, { role: 'user', content: 'Hello' }],
    temperature: 0.2, tool_choice: 'auto', chat_template_kwargs: { enable_thinking: false } };
  const original = structuredClone(payload);
  const first = h.run('before_provider_request', { payload });
  assert.deepEqual(first.tools.map((tool) => tool.function.name), core);
  assert.deepEqual(first.messages, original.messages);
  assert.equal(first.temperature, 0.2);
  assert.deepEqual(first.chat_template_kwargs, original.chat_template_kwargs);
  assert.deepEqual(payload, original, 'Do not mutate the prior hook payload');
  const followup = h.run('before_provider_request', { payload: { ...payload, tools: core.map(schema) } });
  assert.deepEqual(first.tools, followup.tools);
  assert.equal(h.reports.at(-1).request.toolsChanged, false);
  assert.equal(h.reports.at(-1).request.systemChanged, false);
});

test('schema guard respects tool-free requests, missing tools, and legitimate schema changes', () => {
  const h = stableFixture(); h.run('session_start');
  const payload = { messages: [{ role: 'system', content: 'Instructions' }], tools: core.map(schema) };
  h.run('before_provider_request', { payload });
  const changed = structuredClone(payload);
  changed.tools[0].function.description = 'Updated read semantics';
  changed.messages[0].content = 'Updated instructions';
  assert.deepEqual(h.run('before_provider_request', { payload: changed }), changed);
  assert.equal(h.reports.at(-1).request.toolsChanged, true);
  assert.equal(h.reports.at(-1).request.systemChanged, true);
  assert.ok(!JSON.stringify(h.reports).includes('Updated instructions'));
  assert.deepEqual(h.run('before_provider_request', { payload: { ...payload, tools: [schema('read')] } }).tools, [schema('read')]);
  assert.deepEqual(h.run('before_provider_request', { payload: { ...payload, tools: [] } }).tools, []);
  assert.equal(h.run('before_provider_request', { payload: { messages: [] } }), undefined);
});

test('schema stabilization is explicit, API-specific, and resets on model changes and reload', () => {
  const payload = { messages: [], tools: full.map(schema) };
  const h = stableFixture(); h.run('session_start');
  h.run('before_provider_request', { payload });
  h.run('session_shutdown'); h.run('session_start');
  h.run('before_provider_request', { payload });
  assert.equal(h.reports.at(-1).request.toolsChanged, null);
  h.ctx.model = cloud;
  assert.equal(h.run('before_provider_request', { payload }), undefined);
  h.run('model_select');
  h.ctx.model = { ...local, api: 'anthropic-messages' };
  assert.equal(h.run('before_provider_request', { payload }), undefined);
  const unconfigured = fixture({ ...local, api: 'openai-completions' });
  assert.equal(unconfigured.run('before_provider_request', { payload }), undefined);
  h.ctx.model.api = 'openai-completions';
  assert.equal(h.run('before_provider_request', { payload: { ...payload, tools: [{ type: 'custom' }] } }), undefined);
  assert.throws(() => validateProfiles({ profiles: [{ ...config().profiles[0], stableToolSchemas: 'yes' }] }));
});
