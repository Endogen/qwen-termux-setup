// Personal, opt-in model profiles. No app/model-specific policy lives here.
// Explicit profiles live in ../model-profiles.json; unlisted models are untouched.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';

const THINKING = new Set(['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']);
const key = (model) => JSON.stringify([model?.provider, model?.id]);

export function validateProfiles(config) {
  if (!config || !Array.isArray(config.profiles)) throw new Error('model-profiles.json must contain a profiles array');
  const claimed = new Set();
  for (const profile of config.profiles) {
    if (!profile || typeof profile.name !== 'string' || !profile.name.trim() ||
      !Array.isArray(profile.models) || !profile.models.length ||
      !Array.isArray(profile.tools) || !profile.tools.length ||
      profile.tools.some((name) => typeof name !== 'string' || !name.trim()) ||
      new Set(profile.tools).size !== profile.tools.length ||
      (profile.stableToolSchemas !== undefined && typeof profile.stableToolSchemas !== 'boolean') ||
      (profile.thinking !== undefined && !THINKING.has(profile.thinking))) {
      throw new Error('Invalid model profile: provide a name, exact models, unique tools, and an optional thinking level');
    }
    for (const model of profile.models) {
      if (!model || typeof model.provider !== 'string' || !model.provider.trim() ||
        typeof model.id !== 'string' || !model.id.trim() || claimed.has(key(model))) {
        throw new Error('Model profiles require unique, non-empty provider/id pairs');
      }
      claimed.add(key(model));
    }
  }
  return config.profiles;
}

export function installModelProfiles(pi, config, report = () => {}) {
  const profiles = validateProfiles(config);
  const controls = () => ({ tools: [...pi.getActiveTools()], thinking: pi.getThinkingLevel() });
  let baseline;
  let saved;
  let activeKey;
  let previousRequest;

  const profileFor = (model) => profiles.find((item) => item.models.some((match) => key(match) === key(model)));

  function sync(event, ctx) {
    const profile = profileFor(ctx.model);
    if (!profile) {
      if (saved) {
        pi.setActiveTools(saved.tools);
        pi.setThinkingLevel(saved.thinking);
        if (event?.systemPromptOptions) event.systemPromptOptions.selectedTools = [...pi.getActiveTools()];
        saved = undefined; activeKey = undefined;
        previousRequest = undefined;
        ctx.ui?.setStatus?.('model-profile', undefined);
      }
      baseline = controls();
      return;
    }
    const registered = new Set(pi.getAllTools().map((tool) => tool.name));
    const missing = profile.tools.filter((name) => !registered.has(name));
    if (missing.length) throw new Error(`Model profile has unavailable tools: ${missing.join(', ')}`);
    if (!saved) saved = baseline || controls();
    const entering = activeKey !== key(ctx.model);
    if (entering) previousRequest = undefined;
    if (JSON.stringify(pi.getActiveTools()) !== JSON.stringify(profile.tools)) pi.setActiveTools([...profile.tools]);
    if (entering && profile.thinking !== undefined) pi.setThinkingLevel(profile.thinking);
    activeKey = key(ctx.model);
    // before_agent_start's structured options were captured before this hook.
    // Keep its tool section consistent without replacing any user instructions.
    if (event?.systemPromptOptions) event.systemPromptOptions.selectedTools = [...pi.getActiveTools()];
    ctx.ui?.setStatus?.('model-profile', `${profile.name}: ${profile.tools.join(', ')}`);
    try {
      report({ model: { provider: ctx.model.provider, id: ctx.model.id }, profile: profile.name,
        tools: [...pi.getActiveTools()], thinking: pi.getThinkingLevel(),
        systemPromptCharacters: ctx.getSystemPrompt?.().length ?? null });
    } catch { /* Diagnostics must not interfere with a conversation. */ }
  }

  pi.on('session_start', sync);
  pi.on('model_select', sync);
  pi.on('before_agent_start', sync);
  pi.on('before_provider_request', (event, ctx) => {
    const profile = profileFor(ctx.model);
    // Serialized payloads differ across provider APIs. Opt in only for the
    // OpenAI chat-completions shape used by this local provider.
    if (!profile?.stableToolSchemas || ctx.model?.api !== 'openai-completions') return;
    const payload = event.payload;
    if (!payload || !Array.isArray(payload.messages) || !Array.isArray(payload.tools)) return;
    if (payload.tools.some((tool) => tool?.type !== 'function' || typeof tool.function?.name !== 'string')) return;
    // Keep current definitions, including intentional schema updates. Never
    // resurrect missing tools (e.g. a tool-free compaction request).
    const tools = profile.tools.flatMap((name) => payload.tools.filter((tool) => tool.function.name === name));
    const result = { ...payload, tools };
    const hash = (value) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
    const current = {
      toolsHash: hash(tools),
      systemHash: hash(payload.messages.filter((message) => ['system', 'developer'].includes(message.role))),
    };
    try {
      report({ model: { provider: ctx.model.provider, id: ctx.model.id }, profile: profile.name,
        tools: tools.map((tool) => tool.function.name), thinking: pi.getThinkingLevel(),
        request: { ...current,
          removedTools: payload.tools.filter((tool) => !profile.tools.includes(tool.function.name)).map((tool) => tool.function.name),
          toolsChanged: previousRequest ? current.toolsHash !== previousRequest.toolsHash : null,
          systemChanged: previousRequest ? current.systemHash !== previousRequest.systemHash : null },
      });
    } catch { /* No raw prompts are logged; diagnostic failures remain harmless. */ }
    previousRequest = current;
    return result;
  });
  pi.on('tool_call', (event, ctx) => {
    const profile = profiles.find((item) => item.models.some((model) => key(model) === key(ctx.model)));
    // Other extensions can activate loader tools after before_agent_start.
    // Keep those from expanding a compact model back into the full tool set.
    if (profile && !profile.tools.includes(event.toolName)) {
      return { block: true, reason: `The ${profile.name} model profile only enables: ${profile.tools.join(', ')}. Switch to an unrestricted model for other tools.` };
    }
  });
  // Reload/session replacement preserves live tool state. Restore the normal
  // baseline before replacing the runtime so repeated reloads cannot trap a
  // later cloud session in the compact tool set.
  pi.on('session_shutdown', () => {
    if (saved) {
      pi.setActiveTools(saved.tools);
      pi.setThinkingLevel(saved.thinking);
    }
    saved = undefined; activeKey = undefined; baseline = undefined; previousRequest = undefined;
  });
}

export default function modelProfiles(pi) {
  const directory = process.env.PI_CODING_AGENT_DIR || join(homedir(), '.pi', 'agent');
  let config;
  try { config = JSON.parse(readFileSync(join(directory, 'model-profiles.json'), 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return; throw error; }
  installModelProfiles(pi, config, (state) => {
    const destination = join(directory, 'voice');
    mkdirSync(destination, { recursive: true, mode: 0o700 });
    writeFileSync(join(destination, `model-profile-${process.pid}.json`), JSON.stringify(state, null, 2) + '\n', { mode: 0o600 });
  });
}
