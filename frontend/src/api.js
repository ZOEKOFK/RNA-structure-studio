const BASE = '/api'
const DEFAULT_TIMEOUT = 20000

// 全局错误提示：网络中断或服务端 5xx 时通知 App.vue 弹出提示，
// 4xx 属于用户输入问题，由各页面自行展示具体原因。
function notifyError(message) {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('rna:error', { detail: message }))
  }
}

async function request(path, { method = 'GET', headers, body, timeout = DEFAULT_TIMEOUT, signal } = {}) {
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeout)
  const forwardAbort = () => controller.abort()
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', forwardAbort, { once: true })
  }

  let res
  try {
    res = await fetch(`${BASE}${path}`, { method, headers, body, signal: controller.signal })
  } catch (e) {
    if (timedOut) {
      throw new Error(`请求超时（${Math.round(timeout / 1000)} 秒），请稍后重试`)
    }
    if (signal && signal.aborted) {
      throw new Error('请求已取消')
    }
    const message = '无法连接后端服务，请确认 uvicorn 已在 8000 端口启动'
    notifyError(message)
    throw new Error(message)
  } finally {
    clearTimeout(timer)
    if (signal) signal.removeEventListener('abort', forwardAbort)
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const message = data.detail || `请求失败 (${res.status})`
    if (res.status >= 500) notifyError(message)
    throw new Error(message)
  }
  return data
}

function post(path, body, opts = {}) {
  return request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...opts,
  })
}

function get(path, opts = {}) {
  return request(path, opts)
}

function del(path, opts = {}) {
  return request(path, { method: 'DELETE', ...opts })
}

export const api = {
  health: () => get('/health'),
  examples: () => get('/examples'),
  fold: (sequence) => post('/fold', { sequence }),
  pairprob: (sequence) => post('/pairprob', { sequence }),
  mutate: (sequence, position, base) => post('/mutate', { sequence, position, base }),
  // RCSB 检索涉及外部网络，留更长的超时并支持外部 AbortSignal 取消
  tertiary: (sequence, opts) => post('/tertiary', { sequence }, { timeout: 45000, ...opts }),
  deepEmbed: (sequence) => post('/deep/embed', { sequence }),
  deepMutate: (sequence, position, base) => post('/deep/mutate', { sequence, position, base }),
  deepJob: (id) => get(`/deep/jobs/${id}`),
  deepCancel: (id) => del(`/deep/jobs/${id}`),
}
