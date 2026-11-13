/* RSS 订阅管理页面：全部业务数据通过宿主桥接动作读写，不持有凭据。 */
(() => {
  "use strict";

  const sdk = window.NeoBotPlugin;
  const byId = (id) => document.getElementById(id);
  const CONTENT_FORMAT_LABELS = {
    title: "仅标题",
    title_link: "标题和链接",
    title_lead: "标题和开头",
    title_body: "标题和正文",
    title_link_lead: "标题、链接和开头",
  };

  const state = {
    context: null,
    busy: false,
    subscriptions: [],
    editingId: null,
    editingWeight: null,
    maxCount: 7,
    availableWeights: [],
    testDefaultsSet: false,
    backendSupportsTest: true,
    timer: null,
  };

  function live() {
    return Boolean(
      state.context &&
        state.context.available &&
        !state.context.suspended &&
        state.context.mode !== "demo",
    );
  }

  function canManage() {
    return (
      live() &&
      Array.isArray(state.context.permissions) &&
      state.context.permissions.includes("manage")
    );
  }

  function textValue(value) {
    return value === null || value === undefined ? "" : String(value);
  }

  function formatTime(timestamp) {
    if (!timestamp) return "—";
    const date = new Date(Number(timestamp) * 1000);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  }

  function formatClock(timestamp) {
    if (!timestamp) return "—";
    const date = new Date(Number(timestamp) * 1000);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  }

  function setWarning(message) {
    const banner = byId("warning");
    if (message) {
      banner.textContent = message;
      banner.hidden = false;
    } else {
      banner.hidden = true;
      banner.textContent = "";
    }
  }

  function notify(title, message, tone) {
    sdk.notify({ title, message, tone }).catch(() => {});
  }

  function showError(error) {
    if (!error || error.code === "view_suspended") return;
    if (error.code === "action_not_found") {
      notify(
        "插件后端未加载该操作",
        "页面资源可能比插件后端新。请在插件管理中重载插件或重启 NeoBot，再刷新此页面后重试。",
        "err",
      );
      return;
    }
    let message = error.message || "操作未完成，请稍后重试。";
    if (Array.isArray(error.fields) && error.fields.length) {
      const paths = error.fields
        .map((field) => (Array.isArray(field.path) ? field.path.join(".") : ""))
        .filter(Boolean);
      if (paths.length) message += `（字段：${paths.join("、")}）`;
    }
    notify("操作未完成", message, "err");
  }

  async function call(actionId, values) {
    if (!live()) {
      const error = new Error("插件当前不可用。");
      error.code = "unavailable";
      throw error;
    }
    return sdk.action(actionId, values || {});
  }

  async function run(task, options) {
    const quiet = Boolean(options && options.quiet);
    if (state.busy) return;
    state.busy = true;
    updateControls();
    try {
      await task();
    } catch (error) {
      if (!quiet) showError(error);
      else console.warn(error);
    } finally {
      state.busy = false;
      updateControls();
    }
  }

  function updateControls() {
    const available = live();
    const manage = canManage();
    const locked = state.busy || !available;
    byId("refresh").disabled = locked;
    byId("load-articles").disabled = locked;
    document
      .querySelectorAll("#subscription-form input, #subscription-form select")
      .forEach((element) => {
        element.disabled = locked || !manage;
      });
    byId("sub-cancel").disabled = locked || !manage;
    const atLimit = state.subscriptions.length >= state.maxCount;
    byId("sub-submit").disabled =
      locked || !manage || (atLimit && state.editingId === null);
    document
      .querySelectorAll("#send-test input, #send-test select")
      .forEach((element) => {
        element.disabled = locked || !manage;
      });
    byId("test-submit").disabled =
      locked || !manage || state.backendSupportsTest === false;
    byId("test-cancel").disabled = locked;
    document.querySelectorAll("[data-manage]").forEach((element) => {
      element.disabled = locked || !manage;
    });
  }

  function renderStatus(data) {
    byId("status-send").textContent = data.send_enabled
      ? "已开启"
      : "已静音（不发送新闻）";
    byId("status-content").textContent =
      CONTENT_FORMAT_LABELS[data.content_format] || data.content_format;
    byId("status-pull").textContent = `至少间隔 ${data.pull_interval_minutes} 分钟`;
    byId("status-cache").textContent = `${data.cache_days} 天`;
    byId("status-articles").textContent = `${data.article_count} 篇`;
    byId("status-latest-pull").textContent = formatTime(data.latest_pull_at);
    byId("status-counts").textContent = `${data.subscription_count} / ${state.maxCount}`;
    byId("status-interval").textContent = `${data.message_interval_seconds} 秒`;
    byId("status-updated").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
    state.backendSupportsTest = data.supports_send_test === true;
    byId("test-stale").hidden = state.backendSupportsTest;
    if (!state.testDefaultsSet) {
      const content = byId("test-content");
      if (
        Array.from(content.options).some(
          (option) => option.value === data.content_format,
        )
      ) {
        content.value = data.content_format;
      }
      state.testDefaultsSet = true;
    }
    setWarning(data.warning || "");
    updateControls();
  }

  function cell(content, className) {
    const element = document.createElement("td");
    if (className) element.className = className;
    if (content instanceof Node) element.append(content);
    else element.textContent = textValue(content);
    return element;
  }

  function button(label, className, onClick, manage) {
    const element = document.createElement("button");
    element.type = "button";
    element.className = className;
    element.textContent = label;
    if (manage) element.dataset.manage = "1";
    element.addEventListener("click", onClick);
    return element;
  }

  function actionsCell(...buttons) {
    const td = document.createElement("td");
    td.className = "actions-col";
    const wrap = document.createElement("div");
    wrap.className = "actions";
    wrap.append(...buttons);
    td.append(wrap);
    return td;
  }

  function armDelete(element, action) {
    let armed = false;
    let timer = null;
    const original = element.textContent;
    element.addEventListener("click", () => {
      if (!armed) {
        armed = true;
        element.textContent = "再点一次确认";
        timer = window.setTimeout(() => {
          armed = false;
          element.textContent = original;
        }, 3000);
        return;
      }
      if (timer) window.clearTimeout(timer);
      action();
    });
  }

  function clear(element) {
    element.replaceChildren();
  }

  function renderSubscriptions(data) {
    state.subscriptions = Array.isArray(data.items) ? data.items : [];
    state.maxCount = Number(data.max_count) || 7;
    state.availableWeights = Array.isArray(data.available_weights)
      ? data.available_weights
      : [];
    byId("subscription-count").textContent = `${state.subscriptions.length} / ${state.maxCount}`;
    byId("subscription-empty").hidden = state.subscriptions.length > 0;
    const atLimit = state.subscriptions.length >= state.maxCount;
    byId("subscription-form-hint").textContent = state.editingId
      ? "正在编辑，修改 URL 会清空旧缓存"
      : atLimit
        ? "已达订阅上限，删除一条后可继续添加"
        : `还可添加 ${state.maxCount - state.subscriptions.length} 条`;
    renderWeightOptions(state.availableWeights, state.editingWeight);
    const body = byId("subscription-rows");
    clear(body);
    if (!state.subscriptions.length) {
      const row = document.createElement("tr");
      const empty = cell("暂无订阅");
      empty.colSpan = 7;
      empty.className = "empty-row";
      row.append(empty);
      body.append(row);
    }
    for (const item of state.subscriptions) {
      const row = document.createElement("tr");
      row.append(cell(item.name || "（未命名）"));
      row.append(cell(item.nickname || "—"));
      const url = document.createElement("span");
      url.className = "url-cell";
      url.textContent = item.url;
      url.title = item.url;
      row.append(cell(url, "url-cell"));
      const weight = document.createElement("span");
      weight.className = "tag";
      weight.textContent = String(item.weight);
      row.append(cell(weight, "num"));
      row.append(cell(String(item.article_count || 0), "num"));
      row.append(cell(formatTime(item.pull_at) === "—" ? "未拉取" : formatTime(item.pull_at)));
      const edit = button("编辑", "nb-quiet", () => startEdit(item), true);
      const remove = button("删除", "nb-danger", () => {}, true);
      armDelete(remove, () => {
        run(async () => {
          await call("subscriptions.remove", { id: item.id });
          notify("订阅已删除", `「${item.name}」的文章缓存已一并删除。`, "ok");
          if (state.editingId === item.id) resetSubscriptionForm();
          await loadSubscriptions();
          await loadStatus();
        });
      });
      row.append(actionsCell(edit, remove));
      body.append(row);
    }
    updateControls();
  }

  function renderWeightOptions(weights, current) {
    const select = byId("sub-weight");
    const previous =
      current === null || current === undefined ? select.value : String(current);
    const values = new Set(weights.map((value) => Number(value)));
    if (current !== null && current !== undefined) values.add(Number(current));
    const options = [new Option("自动（最小可用权重）", "")];
    for (const value of Array.from(values).sort((left, right) => left - right)) {
      options.push(new Option(`权重 ${value}`, String(value)));
    }
    select.replaceChildren(...options);
    if (
      previous &&
      Array.from(select.options).some((option) => option.value === previous)
    ) {
      select.value = previous;
    } else {
      select.value = "";
    }
  }

  function startEdit(item) {
    state.editingId = item.id;
    state.editingWeight = item.weight;
    byId("subscription-form-title").textContent = "编辑订阅";
    byId("sub-submit").textContent = "保存修改";
    byId("sub-cancel").hidden = false;
    byId("sub-edit-hint").hidden = false;
    byId("sub-name").value = item.name;
    byId("sub-nickname").value = item.nickname || "";
    byId("sub-url").value = item.url;
    renderWeightOptions(state.availableWeights, item.weight);
    byId("subscription-form").scrollIntoView({ block: "nearest" });
    byId("sub-name").focus();
    updateControls();
  }

  function resetSubscriptionForm() {
    state.editingId = null;
    state.editingWeight = null;
    byId("subscription-form-title").textContent = "添加订阅";
    byId("sub-submit").textContent = "添加订阅";
    byId("sub-cancel").hidden = true;
    byId("sub-edit-hint").hidden = true;
    byId("sub-name").value = "";
    byId("sub-nickname").value = "";
    byId("sub-url").value = "";
    byId("sub-weight").value = "";
    renderWeightOptions(state.availableWeights, null);
    updateControls();
  }

  function renderArticles(data) {
    const items = Array.isArray(data.items) ? data.items : [];
    byId("article-empty").hidden = items.length > 0;
    const list = byId("article-list");
    clear(list);
    for (const item of items) {
      const entry = document.createElement("li");
      entry.className = "article-item";
      const title = document.createElement("span");
      title.className = "article-title";
      title.textContent = item.title || "（无标题）";
      const meta = document.createElement("span");
      meta.className = "article-meta";
      const parts = [
        item.source_name || `订阅 ${item.source_id}`,
        formatClock(item.pubdate),
      ];
      if (!item.has_body) parts.push("无正文");
      meta.textContent = parts.join(" · ");
      entry.append(title, meta);
      list.append(entry);
    }
  }

  async function loadStatus() {
    const data = await call("status.get");
    renderStatus(data);
  }

  async function loadSubscriptions() {
    const data = await call("subscriptions.list");
    renderSubscriptions(data);
  }

  async function loadArticles() {
    const data = await call("articles.list", { limit: 20 });
    renderArticles(data);
  }

  async function loadAll() {
    await Promise.all([loadStatus(), loadSubscriptions(), loadArticles()]);
  }

  function renderPlaceholders() {
    const values = [
      "status-send",
      "status-content",
      "status-pull",
      "status-cache",
      "status-articles",
      "status-latest-pull",
      "status-counts",
      "status-interval",
    ];
    values.forEach((id) => {
      byId(id).textContent = "—";
    });
    byId("status-updated").textContent = "";
    setWarning("");
    byId("subscription-count").textContent = "—";
    byId("subscription-empty").hidden = true;
    byId("article-empty").hidden = true;
    clear(byId("subscription-rows"));
    clear(byId("article-list"));
    byId("send-test").hidden = true;
    byId("unavailable").hidden = live();
    updateControls();
  }

  function submitSubscription(event) {
    event.preventDefault();
    if (!canManage()) return;
    const name = byId("sub-name").value.trim();
    const nickname = byId("sub-nickname").value.trim();
    const url = byId("sub-url").value.trim();
    const weightText = byId("sub-weight").value;
    if (!name) {
      notify("请填写名称", "订阅名称不能为空。", "warn");
      byId("sub-name").focus();
      return;
    }
    if (!url) {
      notify("请填写 RSS 地址", "地址必须以 http:// 或 https:// 开头。", "warn");
      byId("sub-url").focus();
      return;
    }
    const payload = { name, nickname, url };
    if (weightText) payload.weight = Number(weightText);
    run(async () => {
      const editing = state.editingId !== null;
      let result;
      if (editing) {
        result = await call("subscriptions.update", {
          id: state.editingId,
          ...payload,
        });
      } else {
        result = await call("subscriptions.create", payload);
      }
      notify(
        editing ? "订阅已更新" : "订阅已添加",
        editing && result.url_changed
          ? "URL 已修改，旧文章缓存已清空。"
          : "该订阅会在用户请求新闻时按需拉取。",
        "ok",
      );
      if (result.warning) {
        setWarning(result.warning);
        notify("订阅数量提醒", result.warning, "warn");
      }
      resetSubscriptionForm();
      await loadSubscriptions();
      await loadStatus();
    });
  }

  function openSendTest() {
    if (!canManage()) return;
    const section = byId("send-test");
    section.hidden = false;
    section.scrollIntoView({ block: "nearest" });
    byId("test-qq").focus();
    updateControls();
  }

  function closeSendTest() {
    byId("send-test").hidden = true;
    byId("test-qq").value = "";
    updateControls();
  }

  function submitSendTest(event) {
    event.preventDefault();
    if (!canManage()) return;
    const qq = byId("test-qq").value.trim();
    if (!/^[1-9][0-9]{4,12}$/.test(qq)) {
      notify("QQ 号格式不正确", "请输入 5-13 位、不以 0 开头的数字 QQ 号。", "warn");
      byId("test-qq").focus();
      return;
    }
    const contentFormat = byId("test-content").value;
    run(async () => {
      const result = await call("system.send_test", {
        qq,
        content_format: contentFormat,
      });
      const title = textValue(result.title).slice(0, 80) || "（无标题）";
      notify(
        "发送测试完成",
        `已向 QQ ${result.destination_id} 发送《${title}》（来源：${result.source_name}，${result.message_chars} 字）。`,
        "ok",
      );
      closeSendTest();
    });
  }

  function bindEvents() {
    byId("refresh").addEventListener("click", () => {
      run(loadAll);
    });
    byId("load-articles").addEventListener("click", () => {
      run(loadArticles);
    });
    byId("test-open").addEventListener("click", openSendTest);
    byId("test-cancel").addEventListener("click", closeSendTest);
    byId("send-test-form").addEventListener("submit", submitSendTest);
    byId("subscription-form").addEventListener("submit", submitSubscription);
    byId("sub-cancel").addEventListener("click", resetSubscriptionForm);
  }

  function registerAnchors() {
    for (const id of [
      "status",
      "subscriptions",
      "subscription-form",
      "send-test",
      "commands",
      "articles",
    ]) {
      const element = byId(id);
      if (element) sdk.registerAnchor(id, element);
    }
  }

  function startPolling() {
    if (state.timer) window.clearInterval(state.timer);
    state.timer = window.setInterval(() => {
      if (!live() || state.busy || document.visibilityState !== "visible") return;
      run(loadStatus, { quiet: true });
    }, 60000);
  }

  async function bootstrap() {
    bindEvents();
    try {
      state.context = await sdk.ready();
    } catch (error) {
      byId("unavailable").hidden = false;
      updateControls();
      return;
    }
    registerAnchors();
    sdk.onContext((context) => {
      const wasLive = live();
      state.context = context;
      byId("unavailable").hidden = live();
      updateControls();
      if (live() && !wasLive) {
        run(loadAll, { quiet: true });
      }
      if (!live()) renderPlaceholders();
    });
    sdk.onGuideStep(() => {});
    if (live()) {
      byId("unavailable").hidden = true;
      await run(loadAll);
    } else {
      renderPlaceholders();
    }
    startPolling();
  }

  bootstrap();
})();
