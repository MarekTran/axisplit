// Geometry uses rendered pixels, so hit slop stays the same on phones and desktops.
// Actual containment beats padded containment; padding never steals another row.
function closestReceiptItem(items, px, py, padding = 12) {
  let best = null;
  let bestScore = [Infinity, Infinity, Infinity];
  for (const item of items) {
    const { x, y, w, h } = item;
    const inside = px >= x && px <= x + w && py >= y && py <= y + h;
    const padded = px >= x - padding && px <= x + w + padding &&
      py >= y - padding && py <= y + h + padding;
    const edgeDistance = Math.hypot(
      Math.max(x - px, 0, px - x - w),
      Math.max(y - py, 0, py - y - h),
    );
    const centerDistance = Math.hypot(px - x - w / 2, py - y - h / 2);
    const score = [inside ? 0 : padded ? 1 : 2, edgeDistance, centerDistance];
    // Lexicographic ranking: containment tier, nearest edge, nearest center.
    if (score[0] < bestScore[0] ||
        (score[0] === bestScore[0] && score[1] < bestScore[1]) ||
        (score[0] === bestScore[0] && score[1] === bestScore[1] && score[2] < bestScore[2])) {
      best = item;
      bestScore = score;
    }
  }
  return best;
}

// Also runnable with Node's built-in test runner, without a frontend bundler.
if (typeof module !== "undefined") module.exports = { closestReceiptItem };
