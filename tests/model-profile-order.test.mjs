// Real Pi discovery + event lifecycle + OpenAI serialization, with an in-process
// HTTP fixture. No model, real credentials, personal extensions, or GPU needed.
// Run: PI_SDK_ROOT=/path/to/@earendil-works/pi-coding-agent npm run test:pi
import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { mkdtemp, mkdir, readFile, writeFile, cp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const root = fileURLToPath(new URL('../', import.meta.url));
const sdk = process.env.PI_SDK_ROOT
  ? pathToFileURL(join(resolve(process.env.PI_SDK_ROOT), 'dist/index.js')).href
  : '@earendil-works/pi-coding-agent';
const { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager } = await import(sdk);
const core = ['read', 'bash', 'edit', 'write'];
const system = (payload) => payload.messages.filter(m => ['system', 'developer'].includes(m.role));
const tools = (payload) => payload.tools.map(t => t.function.name);

// Reproduces pi-subagents 0.82.1's before_agent_start loader reactivation:
// it edits BOTH the structured options and the live tools after profile.sync.
const loaderSource = `
import { readFileSync } from 'node:fs';
export default function(pi) {
  pi.registerTool({name:'subagents_enable', label:'Fixture loader',
    description:'Enable fixture tools', promptSnippet:'Fixture loader is installed.',
    parameters:{type:'object',properties:{}},
    execute:async()=>({content:[{type:'text',text:'unused'}],details:{}})});
  pi.on('before_agent_start', (event) => {
    const selected = event.systemPromptOptions.selectedTools ??= [...pi.getActiveTools()];
    if (!selected.includes('subagents_enable')) selected.push('subagents_enable');
    if (!pi.getActiveTools().includes('subagents_enable'))
      pi.setActiveTools([...pi.getActiveTools(),'subagents_enable']);
    event.systemPromptOptions.appendSystemPrompt = readFileSync(new URL('./instructions.txt', import.meta.url),'utf8');
  });
}`;

async function fixture(t, legacy) {
  const directory = await mkdtemp(join(tmpdir(), 'profile-order-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const agent = join(directory, 'agent');
  const cwd = join(directory, 'workspace');
  const loaderDir = join(agent, 'fixture-loader');
  await mkdir(loaderDir, { recursive: true });
  await mkdir(cwd);
  await writeFile(join(loaderDir, 'package.json'), JSON.stringify({name:'fixture-loader',type:'module',pi:{extensions:['./index.mjs']}}));
  await writeFile(join(loaderDir, 'index.mjs'), loaderSource);
  await writeFile(join(loaderDir, 'instructions.txt'), 'KEEP USER INSTRUCTIONS version-one');
  await writeFile(join(agent, 'settings.json'), JSON.stringify({packages:['./fixture-loader'],extensions:['-builtin:mcp'],compaction:{enabled:false},retry:{enabled:false}}));
  execFileSync('python3', [join(root, 'scripts/install-pi-config.py'), '--agent-dir', agent, '--apply']);
  if (legacy) {
    await mkdir(join(agent, 'extensions'));
    await cp(join(root,'pi/extensions/model-profiles.mjs'),join(agent,'extensions/model-profiles.mjs'));
    await cp(join(root,'pi/extensions/model-profiles.ts'),join(agent,'extensions/model-profiles.ts'));
    const settings = JSON.parse(await readFile(join(agent,'settings.json'),'utf8'));
    settings.packages = ['./fixture-loader'];
    await writeFile(join(agent,'settings.json'),JSON.stringify(settings));
  }
  const previousDir = process.env.PI_CODING_AGENT_DIR;
  process.env.PI_CODING_AGENT_DIR = agent;
  t.after(() => { if (previousDir === undefined) delete process.env.PI_CODING_AGENT_DIR; else process.env.PI_CODING_AGENT_DIR = previousDir; });
  const requests = [];
  let nextRead = false;
  const fixtureFile = join(cwd, 'fixture.txt');
  await writeFile(fixtureFile, 'INTEGRATION_READ_OK');
  const server = createServer(async (req, res) => {
    try {
      assert.equal(req.url, '/v1/chat/completions');
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      requests.push(JSON.parse(Buffer.concat(chunks).toString()));
      const delta = nextRead ? {tool_calls:[{index:0,id:'fixture-read',type:'function',function:{name:'read',arguments:JSON.stringify({path:fixtureFile})}}]} : {content:'OK'};
      const finish = nextRead ? 'tool_calls' : 'stop';
      nextRead = false;
      res.writeHead(200, {'content-type':'text/event-stream'});
      for (const chunk of [{choices:[{index:0,delta,finish_reason:null}]}, {choices:[{index:0,delta:{},finish_reason:finish}]}])
        res.write('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',created:1,model:'fixture',...chunk})+'\n\n');
      res.end('data: [DONE]\n\n');
    } catch (error) { res.writeHead(500); res.end(String(error)); }
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  const models = JSON.parse(await readFile(join(agent,'models.json'),'utf8'));
  models.providers['local-llama'].baseUrl = `http://127.0.0.1:${server.address().port}/v1`;
  models.providers['fixture-cloud'] = {...models.providers['local-llama'],models:[{...models.providers['local-llama'].models[0],id:'unrestricted'}]};
  await writeFile(join(agent,'models.json'),JSON.stringify(models));
  const modelRuntime = await ModelRuntime.create({authPath:join(agent,'auth.json'),modelsPath:join(agent,'models.json'),modelsStorePath:join(agent,'model-cache.json'),allowModelNetwork:false});
  const settingsManager = SettingsManager.create(cwd, agent);
  const resourceLoader = new DefaultResourceLoader({cwd,agentDir:agent,settingsManager});
  await resourceLoader.reload();
  const extensions = resourceLoader.getExtensions();
  assert.deepEqual(extensions.errors, []);
  const order = extensions.extensions.map(e=>e.path);
  assert.equal(order.filter(p=>p.endsWith('model-profiles.ts') || p.endsWith('model-profiles.mjs')).length,1);
  const profileIndex = order.findIndex(p=>p.includes('model-profiles.'));
  const loaderIndex = order.findIndex(p=>p.includes('fixture-loader'));
  assert.equal(profileIndex > loaderIndex, !legacy, `Unexpected order: ${order.join(', ')}`);
  const local = modelRuntime.getModel('local-llama','qwen3.5-4b');
  const cloud = modelRuntime.getModel('fixture-cloud','unrestricted');
  const { session } = await createAgentSession({cwd,agentDir:agent,modelRuntime,model:local,
    resourceLoader,settingsManager,sessionManager:SessionManager.inMemory(cwd),thinkingLevel:'off'});
  t.after(() => session.dispose());
  const errors = [];
  await session.bindExtensions({mode:'json',onError:e=>errors.push(e)});
  const prompt = async text => { await session.prompt(text); assert.deepEqual(errors,[]); assert.equal(session.getLastAssistantText(),'OK'); };
  return { session, requests, prompt, local, cloud, loaderDir, requestRead:()=>{nextRead=true;} };
}

test('real Pi lifecycle reproduces legacy first-request prompt/schema inconsistency', async t => {
  const h = await fixture(t, true);
  await h.prompt('First'); await h.prompt('Second');
  assert.deepEqual(tools(h.requests[0]), core);
  assert.deepEqual(tools(h.requests[1]), core);
  // A minimal loader keeps re-adding itself; the full pi-subagents package also
  // has transcript restoration/pinning hooks. This negative control reproduces
  // the initial mismatch, not every later package-specific state transition.
  assert.match(JSON.stringify(system(h.requests[0])), /Fixture loader is installed/);
  assert.ok(!tools(h.requests[0]).includes('subagents_enable'));
});

test('final profile package stabilizes initial/next/tool turns and preserves instruction and cloud changes', async t => {
  const h = await fixture(t, false);
  await h.prompt('First'); await h.prompt('Second');
  h.requestRead(); await h.prompt('Read the fixture');
  assert.equal(h.requests.length,4,'Read produces a tool call and a post-tool request');
  for (const payload of h.requests) {
    assert.deepEqual(tools(payload),core);
    assert.deepEqual(system(payload),system(h.requests[0]));
    assert.match(JSON.stringify(system(payload)), /KEEP USER INSTRUCTIONS version-one/);
    assert.doesNotMatch(JSON.stringify(system(payload)), /Fixture loader is installed/);
  }
  assert.ok(h.requests[3].messages.some(m=>m.role==='tool' && JSON.stringify(m.content).includes('INTEGRATION_READ_OK')));
  assert.deepEqual(h.session.getActiveToolNames(), core);
  await writeFile(join(h.loaderDir,'instructions.txt'),'KEEP USER INSTRUCTIONS version-two');
  await h.prompt('New instructions');
  assert.match(JSON.stringify(system(h.requests.at(-1))),/KEEP USER INSTRUCTIONS version-two/);
  assert.notDeepEqual(system(h.requests.at(-1)),system(h.requests[0]),'Do not freeze legitimate instruction changes');
  await h.session.setModel(h.cloud, {persist:false});
  await h.prompt('Unrestricted');
  assert.ok(tools(h.requests.at(-1)).includes('subagents_enable'));
  assert.match(JSON.stringify(system(h.requests.at(-1))),/Fixture loader is installed/);
  await h.session.setModel(h.local, {persist:false});
  await h.prompt('Local again');
  assert.deepEqual(tools(h.requests.at(-1)),core);
  assert.doesNotMatch(JSON.stringify(system(h.requests.at(-1))),/Fixture loader is installed/);
});
