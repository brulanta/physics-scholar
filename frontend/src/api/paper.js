import axios from "axios";

const BASE = "http://localhost:8000";

export function uploadPaper(file, userId = "default", strict = false) {
  const form = new FormData();
  form.append("file", file);
  form.append("user_id", userId);
  form.append("strict", strict ? "true" : "false"); // 显式字符串
  return axios.post(`${BASE}/upload`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

export function confirmPaper(docId, confirmedTitle, userId = "default") {
  return axios.post(`${BASE}/confirm`, {
    doc_id: docId,
    confirmed_title: confirmedTitle,
    user_id: userId,
  });
}

export function listPapers(userId = "default") {
  return axios.get(`${BASE}/papers`, { params: { user_id: userId } });
}

// 删除 ingestFromArxiv
// 新增
export function ingestFromUrl(pdfUrls) {
  return request.post("/ingest_from_url", {
    pdf_urls: pdfUrls, // 传数组
    user_id: "default",
  });
}

export function deletePaper(docId, userId = "default") {
  return axios.delete(`${BASE}/papers/${docId}`, {
    params: { user_id: userId },
  });
}
