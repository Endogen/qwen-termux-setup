// Opt-in capture for one diagnostic worker; never modifies a request.
import { readFileSync, writeFileSync, appendFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
export default function (pi) {
  const root = join(homedir(), 'local-llm/qwen3.5-4b/logs');
  let request = 0;
  function enabled(ctx) {
    try {
      const permit = JSON.parse(readFileSync(join(root, 'capture-allow.json'), 'utf8'));
      return ctx.model?.provider === 'local-llama' && ctx.model?.id === 'qwen3.5-4b' &&
        permit.pid === process.pid && permit.sessionId === ctx.sessionManager.getSessionId();
    } catch { return false; }
  }
  pi.on('tool_call', (event, ctx) => {
    if (!enabled(ctx)) return;
    try {
      const permit = JSON.parse(readFileSync(join(root, 'capture-allow.json'), 'utf8'));
      if (!permit.allowedTools) return;
      if (permit.allowedTools.includes(event.toolName) && (event.toolName !== 'read' || event.input?.path?.startsWith(permit.fixtureDirectory + '/'))) return;
      writeFileSync(join(root, `capture-${process.pid}-unexpected-tool.json`), JSON.stringify({ tool: event.toolName, input: event.input }), { mode: 0o600 });
      ctx.abort();
      return { block: true, reason: 'This diagnostic permits only the explicitly requested fixture read, or no tools for its chat checks.' };
    } catch { return { block: true, reason: 'Diagnostic permission check failed' }; }
  });
  pi.on('before_provider_request', (event, ctx) => {
    if (!enabled(ctx)) return;
    try {
      request++;
      writeFileSync(join(root, `capture-${process.pid}-${request}-request.json`), JSON.stringify(event.payload, null, 2) + '\n', { mode: 0o600 });
    } catch { /* No interference. */ }
  });
  pi.on('provider_stream_event', (event, ctx) => {
    if (!enabled(ctx)) return;
    try {
      appendFileSync(join(root, `capture-${process.pid}-${request}-events.jsonl`), JSON.stringify(event.data) + '\n', { mode: 0o600 });
    } catch { /* No interference. */ }
  });
}
