// src/utils/extractLinks.js
import { reactive } from 'vue'

export function extractLinks(text) {
  const seen = new Set()
  const results = []

  // ── 1. arXiv 优先 ──
  const arxivPat = /https?:\/\/arxiv\.org\/(abs|pdf)\/([0-9]{4}\.[0-9]+(?:v\d+)?)/gi
  let m
  while ((m = arxivPat.exec(text)) !== null) {
    const arxivId = m[2]
    const baseId = arxivId.replace(/v\d+$/, '')
    if (seen.has(baseId)) continue
    seen.add(baseId)
    results.push({
      type: 'arxiv',
      arxivId,
      pdfUrl: `https://arxiv.org/pdf/${arxivId}`,
      absUrl: `https://arxiv.org/abs/${arxivId}`,
      displayUrl: `arxiv.org/${baseId}`,
      phase: 'idle',
      errorMsg: '',
      urlOk: null,
    })
  }

  // ── 2. 其他 PDF 链接 ──
  const patterns = [
    /https?:\/\/pdfs\.semanticscholar\.org\/[^\s"'<>）】]+?\.pdf(?:\?[^\s"'<>）】]*)?/gi,
    /https?:\/\/openreview\.net\/pdf[^\s"'<>）】]*/gi,
    /https?:\/\/aclanthology\.org\/[^\s"'<>）】]+?\.pdf/gi,
    /https?:\/\/proceedings\.mlr\.press\/[^\s"'<>）】]+?\.pdf/gi,
    /https?:\/\/dl\.acm\.org\/doi\/pdf\/[^\s"'<>）】]*/gi,
    /https?:\/\/[^\s"'<>）】]+?\.pdf(?:\?[^\s"'<>）】]*)?/gi,  // 通用兜底
  ]

  for (const pat of patterns) {
    pat.lastIndex = 0
    let m2
    while ((m2 = pat.exec(text)) !== null) {
      const url = m2[0]
      if (/arxiv\.org/i.test(url)) continue  // arXiv 已处理
      const key = url.toLowerCase()
      if (seen.has(key)) continue
      seen.add(key)

      const domain = (() => {
        try { return new URL(url).hostname } catch { return url }
      })()
      const filename = url.split('/').pop().split('?')[0].slice(0, 24)

      results.push({
        type: 'url',
        pdfUrl: url,
        absUrl: null,
        displayUrl: `${domain}/${filename}`,
        phase: 'idle',
        errorMsg: '',
        urlOk: null,
      })
    }
  }

  return results.map(l => reactive({ ...l }))
}