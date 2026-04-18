import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import mk from '@traptitech/markdown-it-katex'

// src/utils/markdown.js
const md = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  highlight(str, lang) {
    const code = lang && hljs.getLanguage(lang)
      ? hljs.highlight(str, { language: lang }).value
      : md.utils.escapeHtml(str)

    // 用base64存原始代码，彻底避免转义问题
    const encoded = btoa(unescape(encodeURIComponent(str)))

    return (
      `<div class="code-block-wrap">` +
      `<pre class="code-block"><code class="hljs${lang ? ' language-' + lang : ''}">${code}</code></pre>` +
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
      `</div>`
    )
  }
})

md.use(mk, { throwOnError: false, errorColor: '#cc0000' })

export function renderMarkdown(text) {
  // 加一个预处理：确保代码块后有空行，防止markdown-it解析截断
  const normalized = (text || '')
    .replace(/```(\n|$)/gm, '```\n\n')   // 代码块结束后确保有空行
  return md.render(normalized)
}