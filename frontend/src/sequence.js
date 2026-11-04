// 各视图共用的序列规范化：保留用户原始字符，非法/简并碱基交给后端给出明确报错，
// 避免像以前那样静默删除字符后得到一段错误的短序列。
export const DEGENERATE = 'NRYSWKMBDHV'

export function normalizeSequence(value) {
  const lines = (value || '').split(/\r?\n/)
  const hasHeader = lines.some((l) => l.trimStart().startsWith('>'))
  const body = hasHeader
    ? lines.filter((l) => !l.trimStart().startsWith('>') && !l.trimStart().startsWith(';'))
    : lines
  return body.join('').toUpperCase().replace(/T/g, 'U').replace(/\s/g, '')
}

export function degenerateBases(seq) {
  return [...new Set([...seq].filter((c) => DEGENERATE.includes(c)))].join('')
}
