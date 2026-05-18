import MarkdownIt from "markdown-it";

import hljs from "highlight.js";

import mk from "@traptitech/markdown-it-katex";

// src/utils/markdown.js

const md = new MarkdownIt({
  html: true,

  breaks: true,

  linkify: true,

  highlight(str, lang) {
    const code =
      lang && hljs.getLanguage(lang)
        ? hljs.highlight(str, { language: lang }).value
        : md.utils.escapeHtml(str);

    const encoded = btoa(unescape(encodeURIComponent(str)));
    const langLabel = lang ? `<span class="code-lang">${lang}</span>` : "";

    return (
      `<div class="code-block-wrap">` +
      `<div class="code-block-header">` +
      langLabel +
      `<button class="copy-btn" data-code="${encoded}" onclick="` +
      `(function(btn){` +
      `var text=decodeURIComponent(escape(atob(btn.dataset.code)));` +
      `navigator.clipboard.writeText(text).then(function(){` +
      `btn.classList.add('copied');` +
      `btn.textContent='✓ 已复制';` +
      `setTimeout(function(){btn.classList.remove('copied');btn.textContent='复制'},1500)` +
      `})` +
      `})(this)` +
      `">复制</button>` +
      `</div>` +
      `<pre class="code-block"><code class="hljs${lang ? " language-" + lang : ""}">${code}</code></pre>` +
      `</div>`
    );
  },
});

md.use(mk, { throwOnError: false, errorColor: "#cc0000" });

export function renderMarkdown(text) {
  if (!text) return "";

  // 1. 先把 ref 块从原文中提取出来，替换成占位符

  const placeholder = "<!--REFBLOCK-->";

  let refBlockHtml = "";

  const firstRefIdx = text.indexOf('<ref id="');

  let mdText = text;

  if (firstRefIdx !== -1) {
    const before = text.slice(0, firstRefIdx);

    const refArea = text.slice(firstRefIdx);

    refBlockHtml = buildRefBlockHtml(refArea);

    mdText = before + placeholder;
  }

  // 2. 处理行内 [ref:N] 角标

  mdText = mdText.replace(
    /\[ref:(\d+)\]/g,
    (_, n) =>
      `<sup class="ref-badge" data-ref="${n}" onclick="handleRefClick(${n})">${n}</sup>`,
  );

  // 3. 代码块空行修复

  mdText = mdText.replace(/```(\n|$)/gm, "```\n\n");

  // 4. markdown-it 渲染

  let html = md.render(mdText);

  // 5. 把占位符替换回真正的 ref 块 HTML

  if (refBlockHtml) {
    html = html.replace(placeholder, refBlockHtml);

    // md.render 可能把占位符包在 <p> 里，一并处理

    html = html.replace("<!--REFBLOCK-->", refBlockHtml);
  }

  return html;
}

// 独立出来的 ref 块构建函数

function buildRefBlockHtml(refArea) {
  const items = [];

  const itemReg = /<ref id="(\d+)">([\s\S]*?)<\/ref>/g;

  let match;

  while ((match = itemReg.exec(refArea)) !== null) {
    const id = match[1];

    const inner = match[2].trim();

    const zhMatch = inner.match(/<zh>([\s\S]*?)<\/zh>/);

    const zh = zhMatch ? zhMatch[1].trim() : null;

    const main = inner.replace(/<zh>[\s\S]*?<\/zh>/, "").trim();

    const pipeIdx = main.indexOf(" | ");

    let source = main;

    let excerpt = null;

    if (pipeIdx !== -1) {
      source = main.slice(0, pipeIdx).trim();

      excerpt = main.slice(pipeIdx + 3).trim();
    }

    items.push({ id, source, excerpt, zh });
  }

  if (items.length === 0) return "";

  const itemsHtml = items
    .map(({ id, source, excerpt, zh }) => {
      const excerptHtml = excerpt
        ? `<span class="ref-excerpt">${excerpt}</span>`
        : "";

      const zhHtml = zh ? `<span class="ref-zh">${zh}</span>` : "";

      return `

      <div class="ref-item" id="ref-item-${id}">

        <span class="ref-id">[${id}]</span>

        <span class="ref-body">

          <span class="ref-source">${source}</span>

          ${excerptHtml}

          ${zhHtml}

        </span>

      </div>`;
    })
    .join("");

  return `

    <div class="ref-block">

      <button class="ref-toggle" onclick="toggleRefBlock(this)">

        <svg width="11" height="11" viewBox="0 0 11 11" fill="none">

          <path d="M2 3.5l3.5 4 3.5-4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>

        </svg>

        References <span class="ref-count">${items.length}</span>

      </button>

      <div class="ref-list">

        <div>${itemsHtml}</div>

      </div>

    </div>`;
}

if (typeof window !== "undefined") {
  window.handleRefClick = function (n) {
    const item = document.getElementById(`ref-item-${n}`);

    if (!item) return;

    const block = item.closest(".ref-block");

    if (block && !block.classList.contains("open")) {
      block.classList.add("open");
    }

    setTimeout(() => {
      item.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 150);
  };

  // 新增

  window.toggleRefBlock = function (btn) {
    btn.closest(".ref-block").classList.toggle("open");
  };
}
