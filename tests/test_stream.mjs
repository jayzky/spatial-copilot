import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {setImmediate as nextTick} from 'node:timers/promises';
import test from 'node:test';
import vm from 'node:vm';
import {
  AnalysisClient, AnalysisCancelledError, AnalysisTimeoutError,
  createSSEDecoder, readAnalysisStream,
} from '../src/api/analysis-client.mjs';

const encoder = new TextEncoder();
const frame = (event, data) => 'data: ' + JSON.stringify({event, data}) + '\n\n';

function stream(text, {keepOpen = false, onCancel = () => {}} = {}) {
  return new ReadableStream({
    start(controller) {
      if (text) controller.enqueue(encoder.encode(text));
      if (!keepOpen) controller.close();
    },
    cancel: onCancel,
  });
}

function response(text, options) {
  return new Response(stream(text, options), {headers: {'Content-Type': 'text/event-stream'}});
}

test('UTF-8、CRLF、多行 data 及无结束空行的尾帧在每一个切块位置正确解码', () => {
  const bytes = encoder.encode(': 心跳\r\nevent: message\r\ndata: {"event":"thought",\r\ndata: "data":"中文状态"}\r\n\r\ndata: {"event":"result","data":{"summary":"完成"}}');
  const expected = [{event: 'thought', data: '中文状态'}, {event: 'result', data: {summary: '完成'}}];
  for (let split = 0; split <= bytes.length; split++) {
    const messages = [];
    const decoder = createSSEDecoder(item => {messages.push(item);});
    decoder.push(bytes.subarray(0, split));
    decoder.push(bytes.subarray(split));
    decoder.finish();
    assert.deepEqual(messages, expected, '切块位置 ' + split);
  }
  const messages = [];
  const decoder = createSSEDecoder(item => {messages.push(item);});
  for (const byte of bytes) decoder.push(Uint8Array.of(byte));
  decoder.finish();
  assert.deepEqual(messages, expected);
});

test('支持独立 CR 行结束符，注释与 SSE 元信息不会成为业务事件', () => {
  const messages = [];
  const decoder = createSSEDecoder(item => {messages.push(item);});
  decoder.push(encoder.encode(':ping\rid: 4\rretry: 1000\r\rdata:{"event":"mode","data":{"mode":"local"}}\r\r'));
  decoder.finish();
  assert.deepEqual(messages, [{event: 'mode', data: {mode: 'local'}}]);
});

test('无效 JSON、业务事件格式和损坏的 UTF-8 均明确失败', () => {
  for (const text of ['data: bad\n\n', 'data: null\n\n', 'data: {"event":"result"}\n\n']) {
    assert.throws(() => createSSEDecoder(() => {}).push(encoder.encode(text)), /事件流/);
  }
  const decoder = createSSEDecoder(() => {});
  decoder.push(Uint8Array.of(0xe4));
  assert.throws(() => decoder.finish(), TypeError);
});

test('收到成功结果立即结束并取消剩余读取，后续帧不覆盖结果', async () => {
  let cancelled = false;
  const body = stream(frame('thought', '计算中') + frame('result', {summary: '完成'}) + 'data: bad\n\n', {
    keepOpen: true, onCancel: () => {cancelled = true;},
  });
  const observed = [];
  const result = await readAnalysisStream(body, {onEvent: item => observed.push(item)});
  assert.equal(result.summary, '完成');
  assert.deepEqual(observed, [{event: 'thought', data: '计算中'}]);
  assert.equal(cancelled, true);
  assert.equal(body.locked, false);
});

test('提前断流和结构化 error 不被当作成功', async () => {
  await assert.rejects(readAnalysisStream(stream(frame('thought', '处理中'))), /提前结束/);
  await assert.rejects(readAnalysisStream(stream(
    frame('error', {code: 'analysis_failed', message: '计算失败'}) + frame('result', {summary: '不应出现'}),
  )), /计算失败/);
});

test('停止能中断等待中的读取并释放 reader', async () => {
  const controller = new AbortController();
  let cancelled = false;
  const body = stream('', {keepOpen: true, onCancel: () => {cancelled = true;}});
  const pending = assert.rejects(readAnalysisStream(body, {signal: controller.signal}), {name: 'AbortError'});
  controller.abort();
  await pending;
  assert.equal(cancelled, true);
  assert.equal(body.locked, false);
});

test('停止与超时分别返回可识别的状态', async () => {
  const stopped = new AnalysisClient({fetchImpl: async () => response('', {keepOpen: true})});
  const first = assert.rejects(stopped.run({query: '500米'}), AnalysisCancelledError);
  await nextTick();
  stopped.cancel();
  await first;

  const timed = new AnalysisClient({timeoutMs: 5, fetchImpl: async () => response('', {keepOpen: true})});
  await assert.rejects(timed.run({query: '500米'}), AnalysisTimeoutError);
});

test('新分析撤销旧分析，迟到响应被释放且不写入事件', async () => {
  let resolveOld;
  let oldCancelled = false;
  const observed = [];
  const client = new AnalysisClient({fetchImpl: (_url, request) => {
    if (JSON.parse(request.body).query === 'old') return new Promise(resolve => {resolveOld = resolve;});
    return Promise.resolve(response(frame('thought', 'new') + frame('result', {summary: 'new'})));
  }});
  const old = assert.rejects(client.run({query: 'old'}, {onEvent: event => observed.push(event)}), AnalysisCancelledError);
  const latest = client.run({query: 'new'}, {onEvent: event => observed.push(event)});
  resolveOld(response(frame('thought', 'old') + frame('result', {summary: 'old'}), {
    keepOpen: true, onCancel: () => {oldCancelled = true;},
  }));
  assert.equal((await latest).summary, 'new');
  await old;
  assert.deepEqual(observed, [{event: 'thought', data: 'new'}]);
  assert.equal(client.current, null);
  assert.equal(oldCancelled, true);
});

test('旧请求的 finally 不会解除新请求的取消控制', async () => {
  const resolvers = [];
  const client = new AnalysisClient({fetchImpl: () => new Promise(resolve => resolvers.push(resolve))});
  const old = assert.rejects(client.run({query: 'old'}), AnalysisCancelledError);
  const latest = assert.rejects(client.run({query: 'new'}), AnalysisCancelledError);
  const current = client.current;
  resolvers[0](response(frame('result', {summary: 'old'})));
  await old;
  assert.equal(client.current, current);
  client.cancel();
  resolvers[1](response(frame('result', {summary: 'new'})));
  await latest;
  assert.equal(client.current, null);
});

test('422 详情和错误类型可读，错误响应不会尝试读取结果', async () => {
  const client = new AnalysisClient({fetchImpl: async () => new Response(JSON.stringify({
    detail: [{loc: ['body', 'query'], msg: '查询不能为空'}],
  }), {status: 422, headers: {'Content-Type': 'application/json'}})});
  await assert.rejects(client.run({query: ''}), /body.query: 查询不能为空/);

  const wrongType = new AnalysisClient({fetchImpl: async () => new Response('<html>error</html>')});
  await assert.rejects(wrongType.run({query: '500米'}), /未返回 SSE/);
});

test('请求仍按当前 API 字段序列化，工具事件按原协议传递', async () => {
  const payload = {query: '500米', mode: 'local', custom_point: {lon: 114.4, lat: 30.5}};
  const observed = [];
  const tool = {tool: 'buffer_analysis', arguments: {distance_meters: 500, min_capacity: 0}};
  const client = new AnalysisClient({fetchImpl: async (url, options) => {
    assert.equal(url, '/api/query/stream');
    assert.equal(options.method, 'POST');
    assert.deepEqual(JSON.parse(options.body), payload);
    return response(frame('tool_call', tool) + frame('tool_result', {matched_count: 0}) + frame('result', {summary: '无命中'}));
  }});
  assert.equal((await client.run(payload, {onEvent: event => observed.push(event)})).summary, '无命中');
  assert.deepEqual(observed, [{event: 'tool_call', data: tool}, {event: 'tool_result', data: {matched_count: 0}}]);
});

test('默认 fetch 保持全局接收者，避免浏览器 Illegal invocation', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = function () {
    assert.equal(this, globalThis);
    return Promise.resolve(response(frame('result', {summary: '完成'})));
  };
  try {
    assert.equal((await new AnalysisClient().run({query: '500米'})).summary, '完成');
  } finally {
    globalThis.fetch = previous;
  }
});

function element() {
  return {
    style: {}, classList: {toggle() {}}, children: [], options: [], value: '', hidden: false,
    append(...items) {this.children.push(...items);},
    replaceChildren(...items) {this.children = items;},
    insertRow() {const row = element(); this.children.push(row); return row;},
    insertCell() {const cell = element(); this.children.push(cell); return cell;},
    addEventListener() {}, remove() {},
  };
}

async function loadPage(fetchImpl) {
  const html = await readFile(new URL('../src/api/index.html', import.meta.url), 'utf8');
  const elements = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], element()]));
  elements.get('query-input').value = '500米';
  elements.get('analysis-mode').value = 'local';
  elements.get('buffer-select').options = [{value: '500'}];
  const document = {
    getElementById: id => elements.get(id), createElement: element, querySelectorAll: () => [],
  };
  const map = {removeLayer() {}, invalidateSize() {}, on() {}, getContainer: () => element()};
  const groups = [];
  function group() {
    const layers = [];
    const value = {
      layers, addTo() {return value;}, clearLayers() {layers.length = 0;},
      addLayer(layer) {layers.push(layer);}, eachLayer(fn) {layers.forEach(fn);},
    };
    groups.push(value);
    return value;
  }
  const layer = () => ({addTo() {return this;}, bindPopup() {return this;}});
  const leaflet = {
    map: () => map, tileLayer: layer, control: {zoom: layer}, layerGroup: group, circleMarker: layer,
    geoJSON: feature => {
      if (feature.geometry?.type === 'invalid') throw new Error('地图结果格式无效');
      return {addTo(target) {target.addLayer(feature);}};
    },
  };
  class PageClient extends AnalysisClient {
    constructor(options) {super({...options, fetchImpl});}
  }
  const context = {
    document, L: leaflet, location: {protocol: 'http:'}, console,
    AnalysisClient: PageClient, AnalysisCancelledError,
    fetch: async () => ({ok: true, json: async () => ({waterlog_points: [], shelters: []})}),
    window: {addEventListener() {}},
  };
  const script = html.match(/<script type="module">([\s\S]*?)<\/script>/)[1].replace(/^\s*import .*;$/m, '');
  vm.runInNewContext(script, context);
  await nextTick();
  return {elements, groups};
}

test('实际页面在失败或取消后撤销旧结果，保持导出禁用', async () => {
  const feature = {type: 'Feature', geometry: {type: 'Point', coordinates: [114.4, 30.5]}, properties: {type: 'shelter'}};
  const success = {
    summary: '匹配一处', geojson: {
      type: 'FeatureCollection', features: [feature], parameters: {distance_meters: 500},
      matched_shelters: [{name: '样例设施', capacity: 1000, distance_meters: 30}],
    },
  };
  let call = 0;
  let resolveLateResponse;
  const {elements, groups} = await loadPage(async () => {
    call++;
    if (call === 1) return response(frame('result', success));
    if (call === 2) return response(frame('error', {message: '服务失败'}));
    if (call === 3) return response(frame('result', {summary: '无效', geojson: {
      type: 'FeatureCollection', features: [{geometry: {type: 'invalid'}}],
    }}));
    return new Promise(resolve => {resolveLateResponse = resolve;});
  });
  const submit = elements.get('submit-btn');
  const exportButton = elements.get('btn-export-geojson');
  const table = elements.get('table-body');
  await submit.onclick();
  assert.equal(exportButton.disabled, false);
  assert.equal(table.children[0].children[0].textContent, '样例设施');
  for (let failure = 0; failure < 2; failure++) {
    await submit.onclick();
    assert.equal(groups[0].layers.length, 0);
    assert.equal(exportButton.disabled, true);
    assert.equal(table.children[0].children[0].textContent, '分析未完成');
    assert.match(elements.get('analysis-status').textContent, /未生成分析结果/);
  }
  const pending = submit.onclick();
  await nextTick();
  assert.equal(elements.get('cancel-btn').hidden, false);
  elements.get('cancel-btn').onclick();
  resolveLateResponse(response(frame('result', success)));
  await pending;
  assert.equal(groups[0].layers.length, 0);
  assert.equal(exportButton.disabled, true);
  assert.equal(submit.disabled, false);
  assert.equal(elements.get('cancel-btn').hidden, true);
  assert.match(elements.get('analysis-status').textContent, /已取消.*未生成分析结果/);
});
