import axios from "axios";

const request = axios.create({
  baseURL: "/api",
});

export function uploadPaper(file, userId = "default", strict = false) {
  const form = new FormData();
  form.append("file", file);
  form.append("user_id", userId);
  form.append("strict", strict ? "true" : "false");
  return request.post("/upload", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

export function confirmPaper(docId, confirmedTitle, userId = "default") {
  return request.post("/confirm", {
    doc_id: docId,
    confirmed_title: confirmedTitle,
    user_id: userId,
  });
}

export function listPapers(userId = "default") {
  return request.get("/papers", { params: { user_id: userId } });
}

export function ingestFromUrl(pdfUrls) {
  return request.post("/ingest_from_url", {
    pdf_urls: pdfUrls,
    user_id: "default",
  });
}

export function deletePaper(docId, userId = "default") {
  return request.delete(`/papers/${docId}`, {
    params: { user_id: userId },
  });
}