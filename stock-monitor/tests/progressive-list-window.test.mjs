import assert from 'node:assert/strict';
import test from 'node:test';

import {
  nextVisibleCount,
  takeVisibleItems,
} from '../src/lib/progressive-list.ts';

test('large lists render only the requested initial window', () => {
  const items = Array.from({ length: 8 }, (_, index) => `item-${index + 1}`);

  assert.deepEqual(takeVisibleItems(items, 3), ['item-1', 'item-2', 'item-3']);
  assert.deepEqual(takeVisibleItems(items, 20), items);
});

test('load-more count grows by one page without exceeding the real total', () => {
  assert.equal(nextVisibleCount(50, 50, 385), 100);
  assert.equal(nextVisibleCount(350, 50, 385), 385);
  assert.equal(nextVisibleCount(385, 50, 385), 385);
});
