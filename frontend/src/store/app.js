import { reactive, watch } from "vue";

const saved = JSON.parse(localStorage.getItem("ps-settings") || "{}");

export const settings = reactive({
  theme: saved.theme || "dark",
  fontFamily: saved.fontFamily || "system",
  translation: saved.translation ?? false,
  strict: saved.strict ?? false,
});

export const sessions = reactive({
  list: [], // 不再从 localStorage 初始化，由 ChatPage onMounted 拉取
  currentId: "",
});

watch(
  settings,
  (v) => {
    localStorage.setItem("ps-settings", JSON.stringify(v));
    applyTheme(v.theme);
    applyFont(v.fontFamily);
  },
  { deep: true },
);

// sessions.list 不再 watch 持久化，移除旧的 watch

export function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);

  // 动态切换 hljs 主题
  const existingLink = document.getElementById("hljs-theme");
  if (existingLink) existingLink.remove();

  const link = document.createElement("link");
  link.id = "hljs-theme";
  link.rel = "stylesheet";
  link.href =
    theme === "light"
      ? "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css"
      : "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css";
  document.head.appendChild(link);
}

export const FONTS = {
  system: {
    label: "系统默认",
    css: "'Inter', 'PingFang SC', 'Microsoft YaHei', sans-serif",
  },
  serif: { label: "衬线体", css: "'Georgia', 'Noto Serif SC', serif" },
  mono: { label: "等宽体", css: "'JetBrains Mono', 'Fira Code', monospace" },
  rounded: { label: "圆体", css: "'Varela Round', 'PingFang SC', sans-serif" },
};

export function applyFont(family) {
  document.body.style.fontFamily = FONTS[family]?.css || FONTS.system.css;
}

export function createSession(id, firstMsg) {
  const session = {
    id,
    title: firstMsg.slice(0, 22) + (firstMsg.length > 22 ? "…" : ""),
    createdAt: Date.now(),
  };
  sessions.list.unshift(session);
  sessions.currentId = id;
  return session;
}

export function getSession(id) {
  return sessions.list.find((s) => s.id === id);
}

export function renameSession(id, newTitle) {
  const s = sessions.list.find((s) => s.id === id);
  if (s) s.title = newTitle;
}

export function deleteSession(id) {
  const idx = sessions.list.findIndex((s) => s.id === id);
  if (idx === -1) return;
  sessions.list.splice(idx, 1);
  sessions.currentId = sessions.list[0]?.id || "";
}
