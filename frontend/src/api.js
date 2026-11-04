const BASE = '/api'

// 全局错误提示：网络中断或服务端 5xx 时通知 App.vue 弹出提示，
// 4xx 属于用户输入问题，由各页面自行展示具体原因。
function notifyError(message) {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('rna:error', { detail: message }))
  }
}

async function request(path, options) {
  let res
  try {
    res = await fetch(`${BASE}${path}`, options)
  } catch {
    const message = '无法连接后端服务，请确认 uvicorn 已在 8000 端口启动'
    notifyError(message)
    throw new Error(message)
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const message = data.detail || `请求失败 (${res.status})`
    if (res.status >= 500) notifyError(message)
    throw new Error(message)
  }
  return data
}

function post(path, body) {
  return request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function get(path) {
  return request(path)
}

function del(path) {
  return request(path, { method: 'DELETE' })
}

export const api = {
  health: () => get('/health'),
  examples: () => get('/examples'),
  fold: (sequence) => post('/fold', { sequence }),
  pairprob: (sequence) => post('/pairprob', { sequence }),
  mutate: (sequence, position, base) => post('/mutate', { sequence, position, base }),
  tertiary: (sequence) => post('/tertiary', { sequence }),
  deepEmbed: (sequence) => post('/deep/embed', { sequence }),
  deepMutate: (sequence, position, base) => post('/deep/mutate', { sequence, position, base }),
  deepJob: (id) => get(`/deep/jobs/${id}`),
  deepCancel: (id) => del(`/deep/jobs/${id}`),
}
