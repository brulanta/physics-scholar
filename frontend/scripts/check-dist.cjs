// frontend/scripts/check-dist.cjs
// 守卫：检出"孤立代理对转义被碾碎"的损坏签名（U+FFFD 后紧跟 d8~df 残留）
// 合法的 U+FFFD 字符（如 markdown-it 的替换字符）不会命中，不误报
const fs = require('fs');
const path = require('path');

const distDir = path.resolve(__dirname, '..', '..', 'dist'); // frontend/scripts → 项目根 → dist

if (!fs.existsSync(distDir)) {
  console.error(`❌ 未找到产物目录: ${distDir}`);
  process.exit(1);
}

const SIGNATURE = /(?:\uFFFD|\\uFFFD)d[89a-f][0-9a-f]{2}/gi;
const bad = [];

(function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p);
    else if (/\.(js|mjs|cjs)$/.test(e.name)) {
      const hits = fs.readFileSync(p, 'utf8').match(SIGNATURE);
      if (hits) bad.push(`${p}: ${[...new Set(hits)].slice(0, 5).join(', ')}`);
    }
  }
})(distDir);

if (bad.length) {
  console.error('❌ 产物检出损坏签名:\n' + bad.join('\n'));
  process.exit(1);
}
console.log('✓ dist 产物检查通过');