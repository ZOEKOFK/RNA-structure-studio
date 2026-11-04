const BASE = '/api'

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(data.detail || `请求失败 (${res.status})`)
  }
  return data
}

async function get(path) {
  const res = await fetch(`${BASE}${path}`)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(data.detail || `请求失败 (${res.status})`)
  }
  return data
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
}