"use strict";
const conversation = document.querySelector("#conversation");
const form = document.querySelector("#chat-form");
const question = document.querySelector("#question");
const status = document.querySelector("#status");
const clear = document.querySelector("#clear");
const send = form.querySelector("[type=submit]");
const stopChat = document.querySelector("#stop-chat");
const latestChat = document.querySelector("#latest-chat");
let chatStream;
let followChat = true;
const welcome = document.querySelector("#welcome");
const scrollArea = document.querySelector("#scroll-area");
const historyButton = document.querySelector("#history-button");
const historyDialog = document.querySelector("#history-dialog");
const historyStatus = document.querySelector("#history-status");
const sessionList = document.querySelector("#session-list");
const contextButton = document.querySelector("#context-button");
const contextDialog = document.querySelector("#context-dialog");
const contextForm = document.querySelector("#context-form");
const contextContent = document.querySelector("#context-content");
const contextFile = document.querySelector("#context-file");
const contextImport = document.querySelector("#context-import");
const contextSave = document.querySelector("#context-save");
const contextStatus = document.querySelector("#context-status");
const contextRetry = document.querySelector("#context-retry");
const personaButton = document.querySelector("#persona-button");
const personaOptions = document.querySelector("#persona-options");
const personaSwitcher = personaButton.parentElement;
const persona = new URL(location.href).searchParams.get("persona") === "wizard" ? "wizard" : "munger";
const isWizard = persona === "wizard";
const sessionKey = "wizardj." + persona + ".session";
const maxContextCharacters = 32_000;
const profile = isWizard ? {
  name: "老巫师",
  title: "老巫师 · 运维小助手",
  avatar: "/assistant/wizard.png",
  intro: "你可以让我联网搜索、查找站内文章，或把当前对话整理成草稿。",
  placeholder: "想联网查什么，或想写什么草稿？",
  thinking: ["翻动古籍…", "查阅卷轴…", "推演星象…", "整理笔记…"],
} : {
  name: "老芒格",
  title: "芒格视角",
  avatar: "/assistant/munger.jpeg",
  intro: "嗯，说来听听。让我们用多元思维模型，一起把问题拆开看看。",
  placeholder: "说说你想分析的问题…",
  thinking: ["摘下眼镜…", "擦拭镜片…", "翻看笔记…", "沉思片刻…", "整理思路…"],
};
const thinkingPhrases = profile.thinking;
let activeSession = null;
let contextSource = "";
let contextPreview = "";

function apiUrl(path) {
  const url = new URL("/assistant/api/" + path, location.origin);
  url.searchParams.set("persona", persona);
  return url.pathname + url.search;
}

document.documentElement.dataset.persona = persona;
document.title = profile.title;
document.querySelector("#persona-avatar").src = profile.avatar;
document.querySelector("#persona-name").textContent = profile.name;
personaButton.setAttribute("aria-label", "切换角色，当前为" + profile.name);
function setPersonaOptions(open, restoreFocus = false) {
  personaOptions.hidden = !open;
  personaButton.setAttribute("aria-expanded", String(open));
  if (restoreFocus) personaButton.focus();
}

personaButton.addEventListener("click", () => setPersonaOptions(personaOptions.hidden));
personaSwitcher.addEventListener("keydown", event => {
  if (event.key !== "Escape" || personaOptions.hidden) return;
  event.preventDefault();
  setPersonaOptions(false, true);
});
personaSwitcher.addEventListener("focusout", event => {
  if (event.relatedTarget && !personaSwitcher.contains(event.relatedTarget)) setPersonaOptions(false);
});
document.addEventListener("pointerdown", event => {
  if (!personaSwitcher.contains(event.target) && !personaOptions.hidden) setPersonaOptions(false);
});
for (const option of personaOptions.querySelectorAll("[data-persona-option]")) {
  const target = option.dataset.personaOption;
  const url = new URL(location.href);
  url.searchParams.set("persona", target);
  option.href = url.href;
  if (target === persona) {
    option.setAttribute("aria-current", "true");
    option.addEventListener("click", event => {
      event.preventDefault();
      setPersonaOptions(false, true);
    });
  }
}
const panel = document.querySelector("#assistant-panel");
const panelStack = [];
const panelScroll = new WeakMap();
let panelTrigger;
let panelClosing = false;

function currentPanel() {
  return panelStack.at(-1)?.view;
}

function hidePanelView(view) {
  panelScroll.set(view, view.querySelector(".panel-body").scrollTop);
  view.hidden = true;
}

function revealPanelView(view, direction) {
  view.hidden = false;
  view.dataset.enter = direction;
  view.querySelector(".panel-body").scrollTop = panelScroll.get(view) || 0;
  panel.setAttribute("aria-labelledby", view.querySelector("h2").id);
}

function showPanel(view, trigger = document.activeElement) {
  if (panelClosing || currentPanel() === view) return false;
  const previous = currentPanel();
  if (previous) {
    hidePanelView(previous);
  } else {
    panelTrigger = trigger;
  }
  panelStack.push({view, trigger});
  revealPanelView(view, previous ? "forward" : "");
  view.querySelector(".panel-back").hidden = !previous;
  if (!panel.open) {
    panel.showModal();
    // Commit the offscreen position before starting the entrance transition.
    panel.getBoundingClientRect();
    panel.classList.add("is-visible");
    view.querySelector(".panel-body").scrollTop = panelScroll.get(view) || 0;
  }
  const control = view.querySelector(previous ? ".panel-back" : ".panel-close");
  control.focus({preventScroll: true});
  return true;
}

function leavePanel(view) {
  if (!view.hidden) hidePanelView(view);
  view.dispatchEvent(new Event("panel-leave"));
}

function returnInPanel() {
  if (panelClosing || panelStack.length < 2 || publishBusy) return;
  const {view, trigger} = panelStack.pop();
  leavePanel(view);
  const previous = currentPanel();
  revealPanelView(previous, "back");
  const destination = previous.contains(trigger) && trigger.isConnected && !trigger.disabled
    ? trigger : previous.querySelector(".panel-close");
  destination.focus({preventScroll: true});
}

async function closePanel(destination = panelTrigger) {
  if (!panel.open || panelClosing || publishBusy) return;
  panelClosing = true;
  panel.classList.remove("is-visible");
  // Read the new styles so the exit transition is registered before waiting.
  panel.getBoundingClientRect();
  try {
    await Promise.allSettled(panel.getAnimations().map(animation => animation.finished));
  } finally {
    for (const {view} of panelStack.splice(0).reverse()) leavePanel(view);
    panel.close();
    panelClosing = false;
    destination?.focus({preventScroll: true});
  }
}

let pressedBackdrop = false;
panel.addEventListener("pointerdown", event => {
  pressedBackdrop = event.target === panel;
});
panel.addEventListener("click", event => {
  if (event.target.closest(".panel-back")) returnInPanel();
  else if ((event.target === panel && pressedBackdrop) || event.target.closest(".panel-close")) closePanel();
  pressedBackdrop = false;
});
panel.addEventListener("cancel", event => {
  event.preventDefault();
  closePanel();
});

const toolsDialog = document.querySelector("#tools-dialog");
const toolsButton = document.querySelector("#tools-button");
toolsButton.addEventListener("click", () => showPanel(toolsDialog, toolsButton));
welcome.querySelector("img").src = profile.avatar;
welcome.querySelector("h1").textContent = "你好，我是" + profile.name;
welcome.querySelector("p").textContent = profile.intro;
question.placeholder = profile.placeholder;
for (const element of document.querySelectorAll("[data-wizard-only]")) element.hidden = !isWizard;
document.querySelector("#context-export").href = apiUrl("context/download");

async function requestJson(path, data) {
  const options = data === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(data),
  };
  const response = await fetch(apiUrl(path), options);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.error || "请求失败（" + response.status + "）");
  }
  return response.json();
}

function rememberSession(id) {
  activeSession = id;
  try {
    sessionStorage.setItem(sessionKey, id || "");
  } catch {
    // History remains available if this browser blocks session storage.
  }
}

function rememberedSession() {
  try {
    return sessionStorage.getItem(sessionKey);
  } catch {
    return null;
  }
}

function setChatBusy(busy) {
  if (busy) setPersonaOptions(false);
  send.disabled = clear.disabled = historyButton.disabled = personaButton.disabled = busy;
}

function scrollToLatest() {
  scrollArea.scrollTop = scrollArea.scrollHeight;
  followChat = true;
  latestChat.hidden = true;
}

function updateChatPosition() {
  followChat = scrollArea.scrollHeight - scrollArea.scrollTop - scrollArea.clientHeight < 48;
  latestChat.hidden = followChat;
}

function followLatest() {
  if (followChat) scrollToLatest();
}

scrollArea.addEventListener("scroll", updateChatPosition, {passive: true});
latestChat.addEventListener("click", () => {
  scrollToLatest();
  scrollArea.focus({preventScroll: true});
});
stopChat.addEventListener("click", () => chatStream?.abort());

function composerKey() {
  return "wizardj." + persona + ".composer." + (activeSession || "new");
}

function saveComposer() {
  try {
    if (question.value) sessionStorage.setItem(composerKey(), question.value);
    else sessionStorage.removeItem(composerKey());
  } catch {
    // The editor still works when session storage is unavailable.
  }
}

function restoreComposer() {
  try {
    question.value = sessionStorage.getItem(composerKey()) || "";
  } catch {
    question.value = "";
  }
}

question.addEventListener("input", saveComposer);

question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

function sourceLink(label, target) {
  try {
    const url = new URL(target);
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) return null;
    const link = document.createElement("a");
    link.href = url.href;
    link.textContent = label;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  } catch {
    return null;
  }
}

function renderInline(node, value) {
  const pattern = /\[([^\]\n]+)\]\(([^\s)]+)\)|\*\*([^*\n]+)\*\*|\x60([^\x60\n]+)\x60/g;
  let end = 0;
  for (const match of value.matchAll(pattern)) {
    node.append(value.slice(end, match.index));
    const [, label, url, strong, code] = match;
    const item = label ? sourceLink(label, url) : document.createElement(strong ? "strong" : "code");
    if (item && !label) item.textContent = strong || code;
    node.append(item || match[0]);
    end = match.index + match[0].length;
  }
  node.append(value.slice(end));
}

function renderMarkdown(node, value) {
  node.replaceChildren();
  const lines = value.split(/\r?\n/);
  let list = null;
  let paragraph = null;
  let code = null;
  for (const line of lines) {
    if (/^\s*\x60{3}/.test(line)) {
      if (code) {
        code = null;
      } else {
        const pre = document.createElement("pre");
        code = pre.appendChild(document.createElement("code"));
        node.append(pre);
      }
      list = paragraph = null;
      continue;
    }
    if (code) {
      code.append(line + "\n");
      continue;
    }
    const bullet = line.match(/^\s*(?:([-+*])|(\d+)\.)\s+(.+)/);
    if (bullet) {
      const kind = bullet[2] ? "OL" : "UL";
      if (list?.tagName !== kind) {
        list = node.appendChild(document.createElement(kind));
        if (bullet[2]) list.start = Number(bullet[2]);
      }
      renderInline(list.appendChild(document.createElement("li")), bullet[3]);
      paragraph = null;
      continue;
    }
    list = null;
    const heading = line.match(/^#{1,6}\s+(.+)/);
    const quote = line.match(/^>\s?(.*)/);
    if (heading) {
      renderInline(node.appendChild(document.createElement("h3")), heading[1]);
      paragraph = null;
    } else if (quote) {
      renderInline(node.appendChild(document.createElement("blockquote")), quote[1]);
      paragraph = null;
    } else if (!line.trim()) {
      paragraph = null;
    } else {
      if (!paragraph) paragraph = node.appendChild(document.createElement("p"));
      else paragraph.append(document.createElement("br"));
      renderInline(paragraph, line);
    }
  }
}

function updateSearch(row, data) {
  let section = [...row.children].find(item => item.dataset.query === data.query);
  if (!section) {
    section = document.createElement("section");
    section.className = "search-result";
    section.dataset.query = data.query;
    const statusLine = document.createElement("p");
    statusLine.setAttribute("role", "status");
    const details = document.createElement("div");
    section.append(statusLine, details);
    row.append(section);
  }
  const [statusLine, details] = section.children;
  details.replaceChildren();
  const query = data.query;
  if (data.state === "running") {
    statusLine.textContent = "正在联网搜索：" + query;
    return;
  }
  const links = (data.results || []).map(item => sourceLink(item.title, item.url)).filter(Boolean);
  if (links.length) {
    statusLine.textContent = "已搜索到 " + links.length + " 条来源";
    const list = details.appendChild(document.createElement("ul"));
    for (const link of links) list.appendChild(document.createElement("li")).append(link);
    return;
  }
  statusLine.textContent = data.ok ? "未找到可用来源" : "搜索未完成：" + (data.error || "请稍后重试");
  const retry = document.createElement("button");
  retry.className = "toolbar-button";
  retry.type = "button";
  retry.textContent = "重试搜索";
  details.append(retry);
  retry.addEventListener("click", async () => {
    retry.disabled = true;
    statusLine.textContent = "正在重试联网搜索：" + query;
    try {
      updateSearch(row, {...await requestJson("search/retry", {query}), query});
    } catch (error) {
      updateSearch(row, {query, error: error.message});
    }
    const next = section.querySelector("button, a");
    if (document.activeElement === document.body && next) next.focus();
  });
}

function addMessage(role, content, timestamp, searches = []) {
  const row = document.createElement("article");
  row.className = "message-row " + role;
  const body = document.createElement("div");
  body.className = "message-body";
  const meta = document.createElement("p");
  meta.className = "message-meta";
  const clock = new Intl.DateTimeFormat("zh-CN", {hour: "2-digit", minute: "2-digit"});
  meta.textContent = role === "assistant" ? profile.name : "你";
  if (timestamp) {
    const date = new Date(timestamp);
    if (!Number.isNaN(date.valueOf())) meta.textContent += " · " + clock.format(date);
  }
  const text = document.createElement("div");
  text.className = "message-content";
  if (role === "assistant" && content) renderMarkdown(text, content);
  else text.textContent = content;
  body.append(meta, text);
  const sources = document.createElement("div");
  sources.className = "search-results";
  if (role === "assistant") {
    body.append(sources);
    for (const search of searches) updateSearch(sources, search);
  }
  if (role === "assistant") {
    const avatar = document.createElement("img");
    avatar.className = "message-avatar";
    avatar.src = profile.avatar;
    avatar.alt = "";
    avatar.width = avatar.height = 30;
    row.append(avatar);
    row.hidden = !content;
  }
  row.append(body);
  conversation.append(row);
  return {row, text, sources};
}

function showSession(session) {
  conversation.replaceChildren();
  const messages = session?.messages || [];
  for (const entry of messages) {
    addMessage(entry.role, entry.content, entry.created_at, entry.searches);
  }
  welcome.hidden = Boolean(messages.length);
  status.textContent = "";
  rememberSession(session?.id || null);
  restoreComposer();
  scrollToLatest();
}

async function openSession(id) {
  showSession(await requestJson("session?id=" + encodeURIComponent(id)));
}

function historySelectionFocus() {
  return matchMedia("(pointer: coarse)").matches ? historyButton : question;
}

function renderSessions(sessions) {
  sessionList.replaceChildren();
  for (const session of sessions) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    const title = document.createElement("span");
    const updated = document.createElement("time");
    const date = new Date(session.updated_at);
    button.type = "button";
    button.className = "session-button";
    button.dataset.sessionId = session.id;
    if (session.id === activeSession) button.setAttribute("aria-current", "true");
    title.textContent = session.title || "未命名对话";
    button.append(title);
    if (!Number.isNaN(date.valueOf())) {
      updated.dateTime = session.updated_at;
      updated.textContent = new Intl.DateTimeFormat("zh-CN", {dateStyle: "medium", timeStyle: "short"}).format(date);
      button.append(updated);
    }
    button.addEventListener("click", async () => {
      if (button.disabled || openingSession) return;
      if (session.id === activeSession) {
        closePanel(historySelectionFocus());
        return;
      }
      openingSession = true;
      setChatBusy(true);
      const label = title.textContent;
      title.textContent = label + " · 正在打开…";
      historyStatus.textContent = "";
      const choices = [...sessionList.querySelectorAll("button")];
      for (const choice of choices) choice.disabled = true;
      try {
        const selected = await requestJson("session?id=" + encodeURIComponent(session.id));
        if (currentPanel() !== historyDialog || panelClosing) return;
        showSession(selected);
        updateCurrentSession();
        setChatBusy(false);
        await closePanel(historySelectionFocus());
      } catch (error) {
        historyStatus.textContent = "无法打开对话。" + error.message;
      } finally {
        openingSession = false;
        setChatBusy(false);
        title.textContent = label;
        for (const choice of choices) choice.disabled = false;
      }
    });
    item.append(button);
    sessionList.append(item);
  }
}

function updateCurrentSession() {
  for (const button of sessionList.querySelectorAll("button")) {
    if (button.dataset.sessionId === activeSession) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
  }
}

let loadingSessions = false;
let openingSession = false;

async function refreshSessions() {
  updateCurrentSession();
  if (loadingSessions) return;
  loadingSessions = true;
  historyStatus.textContent = sessionList.children.length ? "" : "正在加载历史记录…";
  try {
    const {sessions} = await requestJson("sessions");
    if (openingSession) return;
    const focusedId = document.activeElement?.dataset.sessionId;
    const body = historyDialog.querySelector(".panel-body");
    const previousScroll = body.scrollTop;
    renderSessions(sessions);
    if (!historyDialog.hidden) body.scrollTop = previousScroll;
    if (focusedId) {
      const focused = [...sessionList.querySelectorAll("button")].find(button => button.dataset.sessionId === focusedId);
      (focused || historyDialog.querySelector(".panel-close")).focus({preventScroll: true});
    }
    historyStatus.textContent = sessions.length ? "" : "还没有保存的对话。";
  } catch (error) {
    historyStatus.textContent = "无法加载历史记录。" + error.message;
  } finally {
    loadingSessions = false;
  }
}

historyButton.addEventListener("click", () => {
  if (showPanel(historyDialog, historyButton)) refreshSessions();
});

async function restoreSession() {
  setChatBusy(true);
  try {
    const remembered = rememberedSession();
    if (remembered === "") return;
    const {sessions, latest_id: latest} = await requestJson("sessions");
    const id = sessions.some(session => session.id === remembered) ? remembered : latest;
    if (id) await openSession(id);
  } catch (error) {
    status.textContent = "无法自动打开上一段对话。" + error.message;
  } finally {
    setChatBusy(false);
  }
}

function setContextBusy(busy) {
  contextContent.disabled = contextSave.disabled = contextImport.disabled = contextButton.disabled = busy;
}

function showContext(content) {
  contextSource = content;
  contextContent.value = content;
  contextPreview = contextContent.value;
}

contextButton.addEventListener("click", () => {
  if (showPanel(contextDialog, contextButton) && !contextButton.disabled) loadContext();
});
contextRetry.addEventListener("click", loadContext);

async function loadContext() {
  if (contextContent.value !== contextPreview) {
    contextStatus.textContent = "有未保存的修改。";
    return;
  }
  contextStatus.textContent = "正在加载上下文…";
  contextRetry.hidden = true;
  setContextBusy(true);
  try {
    const {content} = await requestJson("context");
    showContext(content);
    contextStatus.textContent = "";
    setContextBusy(false);
  } catch (error) {
    contextStatus.textContent = "无法加载已保存的上下文。" + error.message;
    contextButton.disabled = false;
    contextRetry.hidden = false;
  }
}

contextContent.addEventListener("input", () => {
  contextStatus.textContent = contextContent.value === contextPreview ? "" : "有未保存的修改。";
});

contextImport.addEventListener("click", () => contextFile.click());
contextFile.addEventListener("change", async () => {
  const file = contextFile.files[0];
  contextFile.value = "";
  if (!file) return;
  if (file.size > 128_003) {
    contextStatus.textContent = "AGENTS.md 超过 32,000 字符。";
    return;
  }
  setContextBusy(true);
  try {
    const content = new TextDecoder("utf-8", {fatal: true, ignoreBOM: true}).decode(await file.arrayBuffer());
    validateContext(content);
    contextContent.value = content;
    contextStatus.textContent = "已载入文件，请检查后保存。";
  } catch (error) {
    contextStatus.textContent = error instanceof TypeError ? "文件必须是 UTF-8 Markdown 文本。" : error.message;
  } finally {
    setContextBusy(false);
  }
});

function validateContext(content) {
  if ([...content].length > maxContextCharacters) throw new Error("AGENTS.md 超过 32,000 字符。");
  if (content.includes("\0")) throw new Error("AGENTS.md 含有无效字符。");
}

contextForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setContextBusy(true);
  contextStatus.textContent = "正在保存…";
  try {
    const content = contextContent.value === contextPreview ? contextSource : contextContent.value;
    validateContext(content);
    await requestJson("context", {content});
    showContext(content);
    contextStatus.textContent = "已保存上下文。";
  } catch (error) {
    contextStatus.textContent = "无法保存上下文。" + error.message;
  } finally {
    setContextBusy(false);
  }
});

let openTagPreview;
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const prompt = question.value.trim();
  if (!prompt || send.disabled) return;
  const submittedSession = activeSession;
  const submittedAt = new Date();
  welcome.hidden = true;
  const userRow = addMessage("user", prompt, submittedAt).row;
  const answer = addMessage("assistant", "", submittedAt);
  setChatBusy(true);
  chatStream = new AbortController();
  stopChat.hidden = false;
  question.value = "";
  saveComposer();
  let phraseIndex = 0;
  status.textContent = thinkingPhrases[phraseIndex];
  const thinking = setInterval(() => {
    phraseIndex = (phraseIndex + 1) % thinkingPhrases.length;
    status.textContent = thinkingPhrases[phraseIndex];
  }, 2200);
  scrollToLatest();
  let done = false;
  let receivedSession = submittedSession;
  let proposedDraft = null;
  let proposedTags = null;
  let answerText = "";
  try {
    const response = await fetch(apiUrl("chat"), {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({session_id: submittedSession, message: prompt}),
      signal: chatStream.signal,
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.error || "请求失败，请稍后重试。");
    }
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let pending = "";
    try {
      for (;;) {
        const {value, done: end} = await reader.read();
        if (end) break;
        pending += value;
        const events = pending.split("\n\n");
        pending = events.pop();
        for (const frame of events) {
          if (!frame.startsWith("data: ")) continue;
          const data = JSON.parse(frame.slice(6));
          if (typeof data.session_id === "string") receivedSession = data.session_id;
          if (isWizard && data.draft && typeof data.draft.id === "string") proposedDraft = data.draft;
          if (isWizard && data.tags && typeof data.tags.id === "string") proposedTags = data.tags;
          if (data.error) throw new Error(data.error);
          if (data.search && isWizard) {
            clearInterval(thinking);
            status.textContent = "";
            answer.row.hidden = false;
            updateSearch(answer.sources, data.search);
            followLatest();
          }
          if (data.text) {
            clearInterval(thinking);
            status.textContent = "";
            answer.row.hidden = false;
            answerText += data.text;
            answer.text.textContent = answerText;
            followLatest();
          }
          if (data.done) done = true;
        }
      }
    } finally {
      await reader.cancel();
    }
    if (!done) throw new Error("对话响应中断。");
    renderMarkdown(answer.text, answerText);
    const nextInput = question.value;
    if (receivedSession && activeSession !== receivedSession) {
      question.value = "";
      saveComposer();
      rememberSession(receivedSession);
      question.value = nextInput;
      saveComposer();
    }
    status.textContent = "";
    if (proposedDraft) {
      const previewButton = document.createElement("button");
      previewButton.className = "toolbar-button";
      previewButton.type = "button";
      previewButton.textContent = "查看草稿预览";
      if (answer.row.hidden) answer.row.hidden = false;
      answer.text.after(previewButton);
      previewButton.addEventListener("click", () => {
        if (activeSession !== receivedSession) return;
        if (openDraftPanel(previewButton)) showDraftPreview(proposedDraft);
      });
    }
    if (proposedTags) {
      const previewButton = document.createElement("button");
      previewButton.className = "toolbar-button";
      previewButton.type = "button";
      previewButton.textContent = "查看打标预览";
      answer.row.hidden = false;
      answer.text.after(previewButton);
      previewButton.addEventListener("click", () => {
        if (activeSession === receivedSession) openTagPreview(proposedTags, previewButton);
      });
    }
  } catch (error) {
    if (answerText) renderMarkdown(answer.text, answerText);
    else if (!answer.sources.children.length) {
      answer.row.remove();
      userRow.remove();
    }
    welcome.hidden = Boolean(conversation.children.length);
    status.textContent = chatStream.signal.aborted ? "已停止接收回复。" : error.message;
    if (!question.value) {
      question.value = prompt;
      saveComposer();
    }
  } finally {
    clearInterval(thinking);
    chatStream = null;
    const restoreFocus = document.activeElement === stopChat || document.activeElement === send;
    stopChat.hidden = true;
    setChatBusy(false);
    followLatest();
    if (restoreFocus) question.focus({preventScroll: true});
  }
});

clear.addEventListener("click", () => {
  showSession(null);
  question.focus();
});

restoreComposer();
restoreSession();

const memoryDialog = document.querySelector("#memory-dialog");
const memoryStatus = document.querySelector("#memory-status");
const memoryList = document.querySelector("#memory-list");
const memoryRefresh = document.querySelector("#memory-refresh");
const memoryMore = document.querySelector("#memory-more");
let memoryOffset = null;

function showMemory(data, append = false) {
  if (!append) memoryList.replaceChildren();
  for (const item of data.items) {
    const row = document.createElement("li");
    const quote = document.createElement("blockquote");
    quote.textContent = item.evidence;
    row.append(document.createTextNode(item.text), quote);
    memoryList.append(row);
  }
  memoryOffset = data.next_offset;
  memoryMore.hidden = memoryOffset == null;
  memoryStatus.textContent = data.total + " 条记忆 · " + data.pending + " 轮对话待提炼";
  if (data.notice) memoryStatus.textContent += " · " + data.notice;
  if (data.running) memoryStatus.textContent += " · 正在提炼";
}

async function refreshMemory(manual) {
  if (memoryRefresh.disabled) return;
  memoryMore.disabled = memoryRefresh.disabled = true;
  memoryStatus.textContent = manual ? "正在核对并提炼原话…" : "正在读取记忆…";
  try {
    const rendered = memoryList.children.length;
    const data = await requestJson(manual ? "memory/refresh" : "memory", manual ? {} : undefined);
    const items = [...data.items];
    let nextOffset = data.next_offset;
    while (items.length < rendered && nextOffset != null) {
      const next = await requestJson("memory?offset=" + nextOffset);
      items.push(...next.items);
      nextOffset = next.next_offset;
    }
    const body = memoryDialog.querySelector(".panel-body");
    const scroll = body.scrollTop;
    showMemory({...data, items, next_offset: nextOffset});
    if (!memoryDialog.hidden) body.scrollTop = scroll;
  } catch (error) {
    memoryStatus.textContent = error.message;
  } finally {
    memoryMore.disabled = memoryRefresh.disabled = false;
  }
}

document.querySelector("#memory-button").addEventListener("click", event => {
  if (showPanel(memoryDialog, event.currentTarget) && !memoryList.children.length) refreshMemory(false);
});
memoryRefresh.addEventListener("click", () => refreshMemory(true));
memoryMore.addEventListener("click", async () => {
  if (memoryOffset == null) return;
  memoryMore.disabled = memoryRefresh.disabled = true;
  try {
    showMemory(await requestJson("memory?offset=" + memoryOffset), true);
  } catch (error) {
    memoryStatus.textContent = error.message;
  } finally {
    memoryMore.disabled = memoryRefresh.disabled = false;
  }
});

const publishDialog = document.querySelector("#publish-dialog");
const publication = document.querySelector("#publication-preview");
const publicationTitle = document.querySelector("#publication-title");
const publicationBody = document.querySelector("#publication-body");
const publicationTags = document.querySelector("#publication-tags");
const publicationImageNote = document.querySelector("#publication-image-note");
const publicationSource = document.querySelector("#publication-source");
const publicationMarkdown = document.querySelector("#publication-markdown");
const publishStatus = document.querySelector("#publish-status");
const publishActions = document.querySelector("#publish-actions");
const publishClose = document.querySelector("#publish-close");
const publishConfirm = document.querySelector("#publish-confirm");
const publishRefresh = document.querySelector("#publish-refresh");
const publishLink = document.querySelector("#publish-link");
const publishHint = document.querySelector("#publish-hint");
let publishSelection = null;
let publishBusy = false;
let publishRequest = 0;

function setPublishActions(mode) {
  publishActions.hidden = !mode;
  publishConfirm.hidden = publishHint.hidden = mode !== "ready";
  publishRefresh.hidden = mode !== "retry";
  publishLink.hidden = mode !== "published";
}

function showPublished(url) {
  const target = new URL(url, location.origin);
  if (!["https://wizardj.cn", location.origin].includes(target.origin)
      || !target.pathname.startsWith("/archives/") || target.username || target.password) {
    throw new Error("无法打开已发布文章链接");
  }
  publishLink.href = target.href;
  setPublishActions("published");
  publishStatus.textContent = "文章已发布。";
}

async function loadPublicationPreview(selection) {
  const request = ++publishRequest;
  publishSelection = selection;
  setPublishActions("");
  publishStatus.textContent = "正在读取待发布文章…";
  publication.hidden = true;
  publicationBody.replaceChildren();
  publicationSource.open = false;
  publicationSource.hidden = true;
  publicationMarkdown.textContent = "";
  const query = new URLSearchParams({source: selection.source, id: selection.id});
  try {
    const article = await requestJson("publish/preview?" + query);
    if (request !== publishRequest || currentPanel() !== publishDialog) return;
    if (article.id !== selection.id || article.source !== selection.source || typeof article.title !== "string") {
      throw new Error("待发布文章与所选草稿不符");
    }
    publicationTitle.textContent = article.title;
    const tags = Array.isArray(article.tags) ? article.tags.filter(tag => typeof tag === "string" && tag) : [];
    publicationTags.textContent = tags.join(" · ");
    publicationTags.hidden = tags.length === 0;
    const hasBody = typeof article.body === "string" && Boolean(article.body.trim());
    publicationImageNote.hidden = !hasBody || !/!\[[^\]\n]*\]\(/.test(article.body);
    if (hasBody) {
      renderMarkdown(publicationBody, article.body);
      publicationMarkdown.textContent = article.body;
      publicationSource.hidden = false;
    }
    publication.hidden = false;
    if (article.status === "published") {
      showPublished(article.url);
    } else if (article.status === "ready" && hasBody && typeof article.revision === "string" && article.revision) {
      publishSelection = {...selection, revision: article.revision};
      setPublishActions("ready");
      publishStatus.textContent = "";
    } else {
      throw new Error("这份草稿暂时无法发布");
    }

  } catch (error) {
    if (request !== publishRequest || currentPanel() !== publishDialog) return;
    publishStatus.textContent = error.message;
    setPublishActions("retry");
  }
}

function openPublicationPreview(source, id, trigger) {
  if (!isWizard || !id || !showPanel(publishDialog, trigger)) return;
  loadPublicationPreview({source, id});
}

publishDialog.addEventListener("panel-leave", () => {
  publishRequest++;
  publishSelection = null;
});
publishRefresh.addEventListener("click", () => {
  if (publishSelection && !publishBusy) loadPublicationPreview(publishSelection);
});
publishConfirm.addEventListener("click", async () => {
  if (!publishSelection?.revision || publishBusy) return;
  const back = publishDialog.querySelector(".panel-back");
  publishBusy = back.disabled = publishClose.disabled = publishConfirm.disabled = true;
  publishStatus.textContent = "正在构建并发布站点，请等待结果…";
  try {
    const result = await requestJson("publish", publishSelection);
    showPublished(result.url);
    publishLink.focus({preventScroll: true});
  } catch (error) {
    publishStatus.textContent = error.message;
    publishRefresh.hidden = false;
  } finally {
    publishBusy = back.disabled = publishClose.disabled = publishConfirm.disabled = false;
  }
});

const draftDialog = document.querySelector("#draft-dialog");
const draftButton = document.querySelector("#draft-button");
const draftForm = document.querySelector("#draft-form");
const draftInput = document.querySelector("#draft-instruction");
const draftStatus = document.querySelector("#draft-status");
const draftPreview = document.querySelector("#draft-preview");
const draftPropose = document.querySelector("#draft-propose");
const draftConfirm = document.querySelector("#draft-confirm");
const draftDownload = document.querySelector("#draft-download");
const draftPublish = document.querySelector("#draft-publish-preview");
let currentDraft = null;
let draftSession = null;
const savedDrafts = document.querySelector("#saved-drafts");
const savedDraftList = document.querySelector("#saved-draft-list");

async function loadSavedDrafts() {
  try {
    const {drafts} = await requestJson("drafts");
    savedDraftList.replaceChildren();
    savedDrafts.hidden = !drafts.length;
    for (const draft of drafts) {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.className = "session-button";
      link.href = apiUrl("draft/download?id=" + encodeURIComponent(draft.id));
      link.textContent = draft.title;
      if (isWizard) {
        item.className = "session-button saved-draft-item";
        const title = document.createElement("h4");
        title.textContent = draft.title;
        link.className = "toolbar-button";
        link.textContent = "下载 Markdown";
        link.setAttribute("aria-label", link.textContent + "：" + draft.title);
        const actions = document.createElement("div");
        actions.className = "dialog-actions";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "toolbar-button";
        button.setAttribute("aria-label", "预览发布：" + draft.title);
        button.textContent = "预览发布";
        button.addEventListener("click", () => openPublicationPreview("draft", draft.id, button));
        actions.append(link, button);
        item.append(title, actions);
      } else {
        item.append(link);
      }
      savedDraftList.append(item);
    }
  } catch (error) {
    if (!draftStatus.textContent) draftStatus.textContent = error.message;
  }
}

function openDraftPanel(trigger) {
  if (!showPanel(draftDialog, trigger)) return false;
  if (draftSession !== activeSession) {
    currentDraft = null;
    draftPreview.hidden = true;
    draftInput.value = "";
    draftSession = activeSession;
  }
  loadSavedDrafts();
  draftStatus.textContent = activeSession ? "" : "请先开始对话，或从历史记录打开一段对话。";
  draftPropose.disabled = !activeSession;
  return true;
}

draftButton.addEventListener("click", () => openDraftPanel(draftButton));

function showDraftPreview(draft) {
  currentDraft = draft.id;
  document.querySelector("#draft-title").textContent = draft.title;
  document.querySelector("#draft-body").textContent = draft.body;
  draftDownload.hidden = true;
  draftPublish.hidden = true;
  draftPreview.hidden = false;
  draftConfirm.hidden = false;
  draftStatus.textContent = "预览已生成。确认后保存为私有 Hugo 草稿。";
}

draftForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!activeSession || !draftInput.value.trim()) return;
  const submittedSession = activeSession;
  draftButton.disabled = draftPropose.disabled = draftConfirm.disabled = true;
  draftStatus.textContent = "正在根据当前对话写草稿…";
  try {
    const draft = await requestJson("draft/preview", {session_id: submittedSession, instruction: draftInput.value.trim()});
    if (draftSession !== submittedSession || activeSession !== submittedSession) return;
    showDraftPreview(draft);
  } catch (error) {
    draftStatus.textContent = error.message;
  } finally {
    draftButton.disabled = draftConfirm.disabled = false;
    draftPropose.disabled = !activeSession;
  }
});

draftConfirm.addEventListener("click", async () => {
  if (!currentDraft) return;
  const id = currentDraft;
  const session = activeSession;
  draftButton.disabled = draftConfirm.disabled = draftPropose.disabled = true;
  try {
    await requestJson("draft/confirm", {id});
    if (currentDraft !== id || activeSession !== session) return;
    draftConfirm.hidden = true;
    draftDownload.href = apiUrl("draft/download?id=" + encodeURIComponent(id));
    draftDownload.hidden = false;
    draftPublish.hidden = !isWizard;
    draftStatus.textContent = "已保存私有草稿，可以下载。";
    loadSavedDrafts();
  } catch (error) {
    draftStatus.textContent = error.message;
  } finally {
    draftButton.disabled = draftConfirm.disabled = false;
    draftPropose.disabled = !activeSession;
  }
});

draftPublish.addEventListener("click", () => openPublicationPreview("draft", currentDraft, draftPublish));

if (isWizard) {
  const articlesDialog = document.querySelector("#articles-dialog");
  const articlesForm = document.querySelector("#articles-form");
  const articlesQuery = document.querySelector("#articles-query");
  const articlesStatus = document.querySelector("#articles-status");
  const articlesList = document.querySelector("#articles-list");
  const articleDetail = document.querySelector("#article-detail");
  const articleContent = document.querySelector("#article-content");
  const articleDetailStatus = document.querySelector("#article-detail-status");
  const articleRetry = document.querySelector("#article-retry");
  const articlesButton = document.querySelector("#articles-button");
  const articleLink = document.querySelector("#public-article");
  let articleRequest = 0;

  function articleMeta(article) {
    const date = article.date ? String(article.date).slice(0, 10) : "";
    const tags = Array.isArray(article.tags) ? article.tags.join(" · ") : "";
    return [date, tags].filter(Boolean).join(" · ");
  }

  async function openArticle(id, trigger) {
    if (currentPanel() !== articleDetail && !showPanel(articleDetail, trigger)) return;
    articleRetry.onclick = () => openArticle(id);
    articleRetry.hidden = true;
    const request = ++articleRequest;
    articleDetailStatus.textContent = "正在读取文章…";
    articleContent.hidden = true;
    articleDetail.querySelector(".panel-body").scrollTop = 0;
    try {
      const article = await requestJson("article?id=" + encodeURIComponent(id));
      if (request !== articleRequest || currentPanel() !== articleDetail) return;
      document.querySelector("#article-title").textContent = article.title;
      document.querySelector("#article-meta").textContent = articleMeta(article);
      document.querySelector("#article-body").textContent = article.body;
      articleLink.hidden = true;
      if (article.url) {
        const url = new URL(article.url, "https://wizardj.cn");
        if (url.origin === "https://wizardj.cn" && !url.pathname.startsWith("/assistant")) {
          articleLink.href = url.href;
          articleLink.hidden = false;
        }
      }
      articleContent.hidden = false;
      articleDetailStatus.textContent = "";
    } catch (error) {
      if (request === articleRequest) {
        articleDetailStatus.textContent = error.message;
        articleRetry.hidden = false;
      }
    }
  }

  async function loadArticles() {
    const request = ++articleRequest;
    articlesStatus.textContent = "正在查找文章…";
    try {
      const {articles} = await requestJson("articles?q=" + encodeURIComponent(articlesQuery.value.trim()));
      if (request !== articleRequest || currentPanel() !== articlesDialog) return;
      articlesList.replaceChildren();
      for (const article of articles) {
        const item = document.createElement("li");
        const button = document.createElement("button");
        const title = document.createElement("span");
        button.type = "button";
        button.className = "session-button";
        title.textContent = article.title;
        button.append(title);
        const meta = articleMeta(article);
        if (meta) {
          const detail = document.createElement("small");
          detail.textContent = meta;
          button.append(detail);
        }
        item.append(button);
        articlesList.append(item);
        button.addEventListener("click", () => openArticle(article.id, button));
      }
      articlesStatus.textContent = articles.length ? "" : "没有找到匹配的公开文章。";
    } catch (error) {
      if (request === articleRequest) articlesStatus.textContent = error.message;
    }
  }

  articlesButton.addEventListener("click", () => {
    if (showPanel(articlesDialog, articlesButton) && !articlesList.children.length) loadArticles();
  });
  articlesForm.addEventListener("submit", event => {
    event.preventDefault();
    loadArticles();
  });
  for (const view of [articlesDialog, articleDetail]) {
    view.addEventListener("panel-leave", () => { articleRequest += 1; });
  }

  const tagsDialog = document.querySelector("#tags-dialog");
  const tagsForm = document.querySelector("#tags-form");
  const tagsQuery = document.querySelector("#tags-query");
  const tagsLimit = document.querySelector("#tags-limit");
  const tagsCategories = document.querySelector("#tags-categories");
  const tagsPreview = document.querySelector("#tags-preview");
  const tagsConfirm = document.querySelector("#tags-confirm");
  const tagsActions = document.querySelector("#tags-actions");
  const tagsStatus = document.querySelector("#tags-status");
  const tagsChanges = document.querySelector("#tags-changes");
  const tagsUnchanged = document.querySelector("#tags-unchanged");
  const tagsUnchangedList = document.querySelector("#tags-unchanged-list");
  let tagsProposalId = null;
  let tagsBusy = false;

  function clearTagsPreview() {
    tagsProposalId = null;
    tagsChanges.replaceChildren();
    tagsUnchangedList.replaceChildren();
    tagsUnchanged.hidden = true;
    tagsUnchanged.open = false;
    tagsActions.hidden = true;
    tagsStatus.textContent = "";
  }

  function setTagsBusy(busy) {
    tagsBusy = busy;
    for (const control of [tagsQuery, tagsLimit, tagsCategories, tagsPreview, tagsConfirm]) {
      control.disabled = busy;
    }
  }

  function renderTagChanges(changes) {
    for (const change of changes) {
      const item = document.createElement("li");
      item.className = "tag-change";
      const heading = document.createElement("h3");
      heading.textContent = change.title;
      const details = document.createElement("dl");
      for (const [label, tags] of [
        ["现有标签", change.current_tags],
        ["建议新增", change.added_tags],
        ["写入后", change.suggested_tags],
      ]) {
        const term = document.createElement("dt");
        const value = document.createElement("dd");
        term.textContent = label;
        if (label === "建议新增" && tags.length) {
          for (const tag of tags) {
            const badge = value.appendChild(document.createElement("strong"));
            badge.className = "added-tag";
            badge.textContent = tag;
          }
        } else {
          value.textContent = tags.length ? tags.join("、") : "无";
        }
        details.append(term, value);
      }
      item.append(heading, details);
      (change.added_tags.length ? tagsChanges : tagsUnchangedList).append(item);
    }
  }

  function acceptTagPreview({id, changes}) {
    clearTagsPreview();
    renderTagChanges(changes);
    const changedCount = changes.filter(change => change.added_tags.length > 0).length;
    const unchangedCount = changes.length - changedCount;
    tagsUnchanged.hidden = unchangedCount === 0;
    document.querySelector("#tags-unchanged-summary").textContent = unchangedCount + " 篇标签无变化";
    tagsStatus.textContent = changedCount ? `已预览 ${changes.length} 篇，${changedCount} 篇有新增标签建议。` : "这些文章没有新增标签建议。";
    if (changedCount && id) {
      tagsProposalId = id;
      tagsConfirm.textContent = `确认写入 ${changedCount} 篇`;
      tagsActions.hidden = false;
    }
  }

  openTagPreview = (proposal, trigger) => {
    if (tagsBusy) return;
    if (currentPanel() !== tagsDialog && !showPanel(tagsDialog, trigger)) return;
    acceptTagPreview(proposal);
  };
  document.querySelector("#tags-button").addEventListener("click", event => showPanel(tagsDialog, event.currentTarget));
  function invalidateTagPreview() {
    if (!tagsProposalId && !tagsChanges.children.length && !tagsUnchangedList.children.length) return;
    tagsProposalId = null;
    tagsActions.hidden = true;
    tagsStatus.textContent = "下方为上一次的结果，请按新条件生成预览后再写入。";
  }

  tagsForm.addEventListener("input", invalidateTagPreview);
  tagsForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (tagsBusy) return;
    const input = {query: tagsQuery.value.trim(), limit: tagsLimit.valueAsNumber};
    if (tagsCategories.value.trim()) input.categories = tagsCategories.value.trim();
    invalidateTagPreview();
    setTagsBusy(true);
    tagsStatus.textContent = "正在分析文章并生成标签建议…";
    try {
      acceptTagPreview(await requestJson("tags/preview", input));
    } catch (error) {
      tagsStatus.textContent = error.message + "。请重新生成预览。";
    } finally {
      setTagsBusy(false);
    }
  });
  tagsConfirm.addEventListener("click", async () => {
    if (tagsBusy || !tagsProposalId) return;
    setTagsBusy(true);
    tagsStatus.textContent = "正在写入 Hugo 源文件…";
    try {
      const {updated} = await requestJson("tags/confirm", {id: tagsProposalId});
      tagsStatus.textContent = `已更新 ${updated} 篇文章的源文件，网站仍显示上次发布的内容。`;
    } catch (error) {
      tagsStatus.textContent = error.message + "。如需再次操作，请重新生成预览。";
    } finally {
      tagsProposalId = null;
      tagsActions.hidden = true;
      setTagsBusy(false);
    }
  });

  const frontierDialog = document.querySelector("#frontier-dialog");
  const frontierStatus = document.querySelector("#frontier-status");
  const frontierToolStatus = document.querySelector("#frontier-tool-status");
  const frontierStart = document.querySelector("#frontier-start");
  const frontierRefresh = document.querySelector("#frontier-refresh");
  const frontierDownload = document.querySelector("#frontier-download");
  const frontierPublish = document.querySelector("#frontier-publish-preview");
  const frontierKey = "wizardj.wizard.frontierRun";
  let frontierRun = null;
  let frontierTimer;
  let frontierBusy = false;
  try {
    frontierRun = sessionStorage.getItem(frontierKey);
  } catch {
    // The current tab still displays the result when session storage is unavailable.
  }

  function setFrontierToolStatus(message) {
    frontierToolStatus.hidden = !message;
    frontierToolStatus.textContent = message;
  }

  function showFrontierRun(run) {
    setFrontierToolStatus(run.status === "done" ? "草稿已生成"
      : run.status === "failed" ? "试跑失败" : "试跑中");
    frontierStart.disabled = run.status === "running";
    frontierDownload.hidden = run.status !== "done";
    frontierPublish.hidden = run.status !== "done";
    frontierRefresh.hidden = run.status !== "running";
    if (run.status === "done") {
      frontierDownload.href = apiUrl("frontier/download?id=" + encodeURIComponent(run.id));
      frontierStatus.textContent = "私有草稿已生成" + (run.title ? " · " + run.title : "。");
    } else if (run.status === "failed") {
      frontierStatus.textContent = run.error || "试跑失败，可以重新执行。";
    } else {
      const message = "正在抓取来源、生成并核验草稿…";
      if (frontierStatus.textContent !== message) frontierStatus.textContent = message;
      frontierTimer = setTimeout(refreshFrontier, 2200);
    }
  }

  async function refreshFrontier() {
    clearTimeout(frontierTimer);
    if (!frontierRun || frontierBusy) return;
    frontierBusy = true;
    frontierRefresh.disabled = true;
    try {
      showFrontierRun(await requestJson("frontier/run?id=" + encodeURIComponent(frontierRun)));
    } catch (error) {
      frontierStatus.textContent = error.message;
      setFrontierToolStatus("状态读取失败");
      frontierStart.disabled = false;
      frontierRefresh.hidden = false;
    } finally {
      frontierBusy = false;
      frontierRefresh.disabled = false;
    }
  }

  document.querySelector("#frontier-button").addEventListener("click", () => {
    if (showPanel(frontierDialog, document.querySelector("#frontier-button"))) refreshFrontier();
  });
  frontierRefresh.addEventListener("click", refreshFrontier);
  frontierPublish.addEventListener("click", () => openPublicationPreview("frontier", frontierRun, frontierPublish));
  frontierStart.addEventListener("click", async () => {
    if (frontierBusy) return;
    clearTimeout(frontierTimer);
    frontierBusy = frontierStart.disabled = true;
    frontierRefresh.hidden = frontierDownload.hidden = frontierPublish.hidden = true;
    frontierStatus.textContent = "正在启动试跑…";
    setFrontierToolStatus("正在启动…");
    try {
      const run = await requestJson("frontier/start", {});
      frontierRun = run.id;
      try {
        sessionStorage.setItem(frontierKey, run.id);
      } catch {
        // Continue polling in this tab.
      }
      showFrontierRun(run);
    } catch (error) {
      frontierStatus.textContent = error.message;
      setFrontierToolStatus("试跑启动失败");
      frontierStart.disabled = false;
      frontierRefresh.hidden = !frontierRun;
    } finally {
      frontierBusy = false;
    }
  });
  if (frontierRun) refreshFrontier();
}

for (const link of document.querySelectorAll("[data-ops-link]")) {
  const url = new URL(link.href);
  url.searchParams.set("persona", persona);
  link.href = url.href;
}

const entryUrl = new URL(location.href);
if (entryUrl.searchParams.get("panel") === "tools") {
  entryUrl.searchParams.delete("panel");
  history.replaceState(null, "", entryUrl);
  showPanel(toolsDialog, toolsButton);
}
