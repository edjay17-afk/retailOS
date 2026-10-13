/* Run the real upload controller against a small DOM fixture. No browser or live records. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const source = readFileSync(join(__dirname, '../app/web/static/app.js'), 'utf8');
const start = source.indexOf('iform.querySelectorAll(\'.upl\').forEach');
const end = source.indexOf('// picking "fail"', start);
assert.ok(start > 0 && end > start);

class Element {
  constructor() { this.children = []; this.events = {}; this.files = []; this.classList = { toggle() {} }; }
  addEventListener(name, fn) { this.events[name] = fn; }
  setAttribute() {}
  appendChild(child) { this.children.push(child); }
  append(...children) { this.children.push(...children); }
  replaceChildren() { this.children = []; }
}
class Transfer {
  constructor() { this.files = []; this.items = { add: file => this.files.push(file) }; }
}
function fixture(multi = true) {
  const input = new Element(), preview = new Element(), face = new Element(), camera = new Element(), cameraButton = new Element(), counter = new Element();
  const controls = { 'input[type=file]': input, '.upl-prev': preview, '.upl-face b': face, '[data-camera-input]': camera, '[data-take-photo]': cameraButton, '.photo-count': counter };
  face.textContent = 'Add photos';
  const tile = new Element(); tile.querySelector = selector => controls[selector]; tile.hasAttribute = () => multi;
  const revoked = [], lifecycle = {};
  const messages = (key, values) => key.replace(/\{(\w+)\}/g, (_, name) => values[name]);
  new Function('iform', 'document', 't', 'countAnswered', 'window', 'URL', 'DataTransfer', 'fmtSize', source.slice(start, end))(
    { querySelectorAll: () => [tile] }, { createElement: () => new Element() }, messages, () => {},
    { addEventListener: (event, fn) => { lifecycle[event] = fn; } },
    { createObjectURL: file => 'blob:' + file.name, revokeObjectURL: url => revoked.push(url) }, Transfer, bytes => String(bytes)
  );
  return { input, preview, camera, counter, revoked, lifecycle };
}
const photo = name => ({ name, size: 20, lastModified: 123, type: 'image/png' });
function choose(input, files) { input.files = files; input.events.change(); }

test('photos accumulate, individual removal keeps the others, and cancel keeps the selection', () => {
  const { input, preview, camera, counter, revoked, lifecycle } = fixture();
  const a = photo('a.png'), b = photo('b.png'), c = photo('c.png');
  choose(input, [a, b]);
  assert.deepEqual(input.files, [a, b]); assert.equal(preview.children.length, 2);
  choose(camera, [c]); assert.deepEqual(input.files, [a, b, c]);
  choose(input, [a]); assert.deepEqual(input.files, [a, b, c]); // selecting a known photo keeps other photos
  preview.children[1].children[1].events.click();
  assert.deepEqual(input.files, [a, c]); assert.equal(counter.textContent, '2 photos added');
  choose(input, []); assert.deepEqual(input.files, [a, c]);
  lifecycle.pagehide(); assert.ok(revoked.includes('blob:b.png'));
  preview.children[0].children[1].events.click(); preview.children[0].children[1].events.click();
  assert.deepEqual(input.files, []); assert.equal(preview.hidden, true);
});

test('each photo item keeps its own files and a video selection replaces only the video', () => {
  const first = fixture(), second = fixture(), video = fixture(false);
  const a = photo('a.png'), b = photo('b.png');
  choose(first.input, [a]); choose(second.input, [b]);
  choose(video.input, [{ ...a, type: 'video/mp4' }]); choose(video.input, [{ ...b, type: 'video/mp4' }]);
  assert.deepEqual(first.input.files, [a]); assert.deepEqual(second.input.files, [b]);
  assert.equal(video.input.files.length, 1); assert.equal(video.input.files[0].name, 'b.png');
});
