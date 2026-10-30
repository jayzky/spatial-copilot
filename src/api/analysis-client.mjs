export class AnalysisCancelledError extends Error {
  constructor() {
    super('已取消本次分析');
    this.name = 'AnalysisCancelledError';
  }
}

export class AnalysisTimeoutError extends Error {
  constructor() {
    super('请求超时，请重试');
    this.name = 'AnalysisTimeoutError';
  }
}

// 按行解码，兼容 UTF-8 跨块、CRLF 跨块及一个事件的多行 data。
export function createSSEDecoder(onMessage) {
  const decoder = new TextDecoder('utf-8', {fatal: true});
  let buffer = '', data = [], stopped = false;

  function dispatch() {
    if (!data.length) return;
    const text = data.join('\n');
    data = [];
    let item;
    try {
      item = JSON.parse(text);
    } catch {
      throw new Error('事件流包含无效 JSON');
    }
    if (!item || typeof item.event !== 'string' || !Object.hasOwn(item, 'data')) {
      throw new Error('事件流格式无效');
    }
    stopped = onMessage(item) === false;
  }

  function line(text) {
    if (!text) return dispatch();
    const colon = text.indexOf(':');
    const field = colon < 0 ? text : text.slice(0, colon);
    if (field !== 'data') return;
    let value = colon < 0 ? '' : text.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    data.push(value);
  }

  function consume(final) {
    while (!stopped) {
      const boundary = buffer.search(/[\r\n]/);
      if (boundary < 0) break;
      if (!final && buffer[boundary] === '\r' && boundary === buffer.length - 1) break;
      const width = buffer.slice(boundary, boundary + 2) === '\r\n' ? 2 : 1;
      const text = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + width);
      line(text);
    }
    if (final && !stopped) {
      if (buffer) line(buffer);
      buffer = '';
      dispatch();
    }
  }

  return {
    push(bytes) {
      if (stopped) return;
      buffer += decoder.decode(bytes, {stream: true});
      consume(false);
    },
    finish() {
      if (stopped) return;
      buffer += decoder.decode();
      consume(true);
    },
  };
}

export async function readAnalysisStream(body, {signal, onEvent = () => {}} = {}) {
  if (!body) throw new Error('服务未返回事件流');
  const reader = body.getReader();
  let result;
  const parser = createSSEDecoder(item => {
    signal?.throwIfAborted();
    if (item.event === 'error') {
      throw new Error(item.data?.message ?? '空间分析失败');
    }
    if (item.event === 'result') {
      result = item.data;
      return false;
    }
    onEvent(item);
  });
  // cancel 会唤醒等待中的 read，即使底层 fetch/测试流未主动抛出 AbortError。
  const abort = () => { reader.cancel().catch(error => console.warn('取消事件流失败：', error.name)); };
  signal?.addEventListener('abort', abort, {once: true});
  try {
    while (result === undefined) {
      signal?.throwIfAborted();
      const {value, done} = await reader.read();
      signal?.throwIfAborted();
      if (done) {
        parser.finish();
        break;
      }
      parser.push(value);
    }
    if (result === undefined) throw new Error('事件流提前结束，尚未取得分析结果');
    return result;
  } finally {
    signal?.removeEventListener('abort', abort);
    try {
      await reader.cancel();
    } catch (error) {
      console.warn('事件流清理失败：', error.name);
    }
    reader.releaseLock();
  }
}

export class AnalysisClient {
  constructor({url = '/api/query/stream', fetchImpl = globalThis.fetch.bind(globalThis), timeoutMs = 65000} = {}) {
    this.url = url;
    this.fetchImpl = fetchImpl;
    this.timeoutMs = timeoutMs;
    this.current = null;
  }

  cancel() {
    this.current?.controller.abort();
  }

  async run(payload, {onEvent = () => {}} = {}) {
    this.cancel();
    const task = {controller: new AbortController(), timedOut: false};
    this.current = task;
    const {signal} = task.controller;
    const checkCurrent = () => {
      signal.throwIfAborted();
      if (this.current !== task) throw new AnalysisCancelledError();
    };
    const timer = setTimeout(() => {
      task.timedOut = true;
      task.controller.abort();
    }, this.timeoutMs);
    try {
      const response = await this.fetchImpl(this.url, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        signal, body: JSON.stringify(payload),
      });
      if (signal.aborted || this.current !== task) {
        try {
          await response.body?.cancel();
        } catch (error) {
          console.warn('清理已取消的响应失败：', error.name);
        }
      }
      checkCurrent();
      if (!response.ok) {
        let message = '请求失败，HTTP ' + response.status;
        try {
          const body = await response.json();
          if (Array.isArray(body.detail)) {
            message = body.detail.map(error => error.loc.join('.') + ': ' + error.msg).join('\n');
          }
        } catch {
          message += '（服务未返回可读错误详情）';
        }
        throw new Error(message);
      }
      if (!response.headers.get('content-type')?.toLowerCase().startsWith('text/event-stream')) {
        throw new Error('服务未返回 SSE 事件流');
      }
      const result = await readAnalysisStream(response.body, {
        signal, onEvent: item => {checkCurrent(); onEvent(item);},
      });
      checkCurrent();
      return result;
    } catch (error) {
      if (signal.aborted) {
        throw task.timedOut ? new AnalysisTimeoutError() : new AnalysisCancelledError();
      }
      throw error;
    } finally {
      clearTimeout(timer);
      if (this.current === task) this.current = null;
    }
  }
}
