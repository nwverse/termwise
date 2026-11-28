import { expect, test } from 'bun:test'

import { annotate } from './annotate'

const dict = new Map([['ephemeral', '短暂']])

test('tilde fences stay untouched and prose is still annotated', () => {
  expect(annotate('~~~text\nephemeral\n~~~\nephemeral in prose.', dict))
    .toBe('~~~text\nephemeral\n~~~\nephemeral（短暂） in prose.')
})

test('double-backtick spans stay untouched and prose is still annotated', () => {
  expect(annotate('``ephemeral``\nephemeral in prose.', dict))
    .toBe('``ephemeral``\nephemeral（短暂） in prose.')
})

test('longer backtick runs may contain shorter runs', () => {
  expect(annotate('``ephemeral ` nested`` and ephemeral outside.', dict))
    .toBe('``ephemeral ` nested`` and ephemeral（短暂） outside.')
})

test('an unclosed backtick run is prose', () => {
  expect(annotate('An `ephemeral note.', dict)).toBe('An `ephemeral（短暂） note.')
})

test('each fenced block is protected independently', () => {
  expect(annotate('```\nephemeral\n```\nprose ephemeral\n~~~\nephemeral\n~~~\nephemeral last', dict))
    .toBe('```\nephemeral\n```\nprose ephemeral（短暂）\n~~~\nephemeral\n~~~\nephemeral last')
})
