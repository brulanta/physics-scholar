import axios from 'axios'

const BASE = 'http://localhost:8000'

export function uploadPaper(file, userId = 'default', strict = false) {
  const form = new FormData()
  form.append('file', file)
  form.append('user_id', userId)
  form.append('strict', strict)
  return axios.post(`${BASE}/upload`, form, {
    headers: { 'Content-Type': 'multipart/form-data' }
  })
}

export function confirmPaper(docId, confirmedTitle, userId = 'default') {
  return axios.post(`${BASE}/confirm`, {
    doc_id: docId,
    confirmed_title: confirmedTitle,
    user_id: userId
  })
}

export function listPapers(userId = 'default') {
  return axios.get(`${BASE}/papers`, { params: { user_id: userId } })
}

export function ingestFromArxiv(arxivIds, userId = 'default') {
  return axios.post(`${BASE}/ingest_from_arxiv`, arxivIds, {
    params: { user_id: userId }
  })
}