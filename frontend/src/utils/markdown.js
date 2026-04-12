import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import mk from '@traptitech/markdown-it-katex'

const md = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  highlight(str, lang) {
    const code = lang && hljs.getLanguage(lang)
      ? hljs.highlight(str, { language: lang }).value
      : md.utils.escapeHtml(str)
    const escaped = str.replace(/`/g, '\\`').replace(/\$/g, '\\$')
    return (
      `<div class="code-block-wrap">` +
      `<pre class="code-block"><code class="hljs${lang ? ' language-' + lang : ''}">${code}</code></pre>` +
      `<button class="copy-btn" onclick="(function(btn){` +
        `navigator.clipboard.writeText(\`${escaped}\`).then(()=>{` +
          `btn.classList.add('copied');btn.textContent='✓ 已复制';` +
          `setTimeout(()=>{btn.classList.remove('copied');btn.textContent='复制'},1500)` +
        `})` +
      `})(this)">复制</button>` +
      `</div>`
    )
  }
})

md.use(mk, { throwOnError: false, errorColor: '#cc0000' })

export function renderMarkdown(text) {
  return md.render(text || '')
}