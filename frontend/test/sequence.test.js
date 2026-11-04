import { test } from 'node:test'
import assert from 'node:assert/strict'
import { normalizeSequence, degenerateBases } from '../src/sequence.js'

test('去掉 FASTA 头与注释行', () => {
  assert.equal(normalizeSequence('>seq1\nGGGAAACCC'), 'GGGAAACCC')
  assert.equal(normalizeSequence('; comment\n>h\nACGU\nACGU'), 'ACGUACGU')
})

test('统一大写、T 转 U、去除空白', () => {
  assert.equal(normalizeSequence('acg t\nacgu'), 'ACGUACGU')
})

test('不静默删除非法字符，交由后端报错', () => {
  assert.equal(normalizeSequence('ACGUXYZ'), 'ACGUXYZ')
})

test('简并碱基识别', () => {
  assert.equal(degenerateBases('ACGUNRYS'), 'NRYS')
  assert.equal(degenerateBases('ACGU'), '')
})

test('空输入返回空串', () => {
  assert.equal(normalizeSequence(''), '')
  assert.equal(normalizeSequence(null), '')
})
