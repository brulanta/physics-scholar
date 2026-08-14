// dev-only：Prompt 模块调控 GUI 的 API（后端 /api/dev 组，frozen 下 404）
import axios from "axios";

const request = axios.create({
  baseURL: "/api/dev",
});

// 模块清单 + 当前 yaml 生效状态 + 内容原文（只读查看）
export function getPromptConfig(mode) {
  return request.get(`/prompt/${mode}`);
}

// dry-run 拼装预览（不落盘）。modules: [{name, enabled}]，列表序即拼装序
export function previewPrompt(mode, modules) {
  return request.post("/prompt/preview", { mode, modules });
}

// 校验 + 渲染 + 原子落盘 profiles/{mode}.yaml（下一问生效，无需重启）
export function savePromptConfig(mode, modules) {
  return request.post("/prompt/save", { mode, modules });
}
