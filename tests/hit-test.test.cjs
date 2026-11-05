const { test } = require('node:test');
const assert = require('node:assert/strict');
const { closestReceiptItem } = require('../src/axisplit/static/hit-test.js');

// Exact normalized OCR boxes from the reported receipt, rendered at two sizes.
function boxes(width, height) {
  return [
    { id: 'mango', x: .23 * width, y: .41 * height, w: .48 * width, h: .02 * height },
    { id: 'watermelon', x: .23 * width, y: .43 * height, w: .48 * width, h: .02 * height },
  ];
}

for (const [width, height] of [[726, 1578], [360, 783]]) {
  test(`Watermelon is not stolen by Mango's padding (${width}px)`, () => {
    const items = boxes(width, height);
    // Tap near the top of Watermelon's box, within both padded hit areas.
    assert.equal(closestReceiptItem(items, .47 * width, .433 * height).id, 'watermelon');
    assert.equal(closestReceiptItem([...items].reverse(), .47 * width, .433 * height).id, 'watermelon');
  });

  test(`Tap just below the OCR box still selects Watermelon (${width}px)`, () => {
    assert.equal(closestReceiptItem(boxes(width, height), .47 * width, .454 * height).id, 'watermelon');
  });

  test(`Mango remains selectable (${width}px)`, () => {
    assert.equal(closestReceiptItem(boxes(width, height), .47 * width, .42 * height).id, 'mango');
  });
}

test('Overlapping padded areas choose the closer box rather than the first', () => {
  const items = [
    { id: 'mango', x: 0, y: 100, w: 200, h: 20 },
    { id: 'watermelon', x: 0, y: 130, w: 200, h: 20 },
  ];
  assert.equal(closestReceiptItem(items, 100, 127).id, 'watermelon');
  assert.equal(closestReceiptItem([...items].reverse(), 100, 127).id, 'watermelon');
});

test('Overlapping actual boxes use nearest center', () => {
  const items = [
    { id: 'mango', x: 0, y: 100, w: 200, h: 30 },
    { id: 'watermelon', x: 0, y: 120, w: 200, h: 30 },
  ];
  assert.equal(closestReceiptItem(items, 100, 129).id, 'watermelon');
});

test('Far taps still snap; an empty receipt returns no match', () => {
  assert.equal(closestReceiptItem(boxes(360, 783), 170, 750).id, 'watermelon');
  assert.equal(closestReceiptItem([], 170, 750), null);
});
