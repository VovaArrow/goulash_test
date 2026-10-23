const { test } = require('node:test');
const assert = require('node:assert/strict');
const { webcrypto } = require('node:crypto');
const { readFileSync } = require('node:fs');
const { runInNewContext } = require('node:vm');

test('Alpine state initializes on HTTP without crypto.randomUUID', () => {
  let onInit, factory;
  runInNewContext(readFileSync(new URL('../static/app.js', 'file://' + __filename), 'utf8'), {
    crypto: { getRandomValues: bytes => webcrypto.getRandomValues(bytes) },
    document: { addEventListener: (_, callback) => { onInit = callback; } },
    Alpine: { data: (_, callback) => { factory = callback; } },
  });
  onInit();
  const app = factory();
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  assert.match(app._demoCollectionKey, uuid);
  assert.match(app._realCollectionKey, uuid);
  assert.notEqual(app._demoCollectionKey, app._realCollectionKey);
});
