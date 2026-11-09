/* RSS 订阅管理页面：全部业务数据通过宿主桥接动作读写，不持有凭据。 */
(() => {
  "use strict";

  const sdk = window.NeoBotPlugin;
  const byId = (id) => document.getElementById(id);
  const SEND_FORMAT_LABELS = {
    merged: "尽可能合并",
    per_source: "分订阅发送",
    per_article: "分条发送",
  };
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
    initialized: false,
    subscriptions: [],
    destinations: [],
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
    return live() && Array.isArray(state.context.permissions) && state.context.permissions.includes("manage");
  }

  function canOperate() {
    return (
      live() &&
      Array.isArray(state.context.permissions) &&
      (state.context.permissions.includes("operate") ||
        state.context.permissions.includes("manage"))
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
    const operate = canOperate();
    const locked = state.busy || !available;
    byId("refresh").disabled = locked;
    byId("pull-now").disabled = locked || !operate;
    byId("load-articles").disabled = locked;
    document
      .querySelectorAll("#subscription-form input, #subscription-form select")
      .forEach((element) => {
        element.disabled = locked || !manage;
      });
    document
      .querySelectorAll("#destination-form input")
      .forEach((element) => {
        element.disabled = locked || !manage;
      });
    byId("dest-submit").disabled = locked || !manage;
    byId("sub-cancel").disabled = locked || !manage;
    document
      .querySelectorAll("#send-test input, #send-test select")
      .forEach((element) => {
        element.disabled = locked || !manage;
      });
    byId("test-submit").disabled =
      locked || !manage || state.backendSupportsTest === false;
    byId("test-cancel").disabled = locked;
    const atLimit = state.subscriptions.length >= state.maxCount;
    byId("sub-submit").disabled =
      locked || !manage || (atLimit && state.editingId === null);
    document.querySelectorAll("[data-manage]").forEach((element) => {
      element.disabled = locked || !manage;
    });
  }

  function renderStatus(data) {
    byId("status-send").textContent = data.send_enabled
      ? `已开启 · ${SEND_FORMAT_LABELS[data.send_format] || data.send_format}`
      : "未开启（默认不发送）";
    byId("status-send").title = `发送内容：${CONTENT_FORMAT_LABELS[data.content_format] || data.content_format}`;
    byId("status-cycle").textContent = `每 ${data.send_every_days} 天 ${data.send_time}（${data.timezone}）`;
    byId("status-next").textContent = !data.send_enabled
      ? "未启用定期发送"
      : data.next_send
        ? formatTime(data.next_send)
        : "暂无启用的接收者";
    byId("status-pull").textContent = `每 ${data.pull_interval_minutes} 分钟`;
    byId("status-articles").textContent = `${data.article_count} 篇`;
    byId("status-articles").title = `已发送记录 ${data.sent_count} 条`;
    byId("status-latest-pull").textContent = formatTime(data.latest_pull_at);
    byId("status-counts").textContent = `${data.subscription_count} / ${data.destination_count}`;
    byId("status-running").textContent = data.running ? "运行中" : "已停止";
    byId("status-updated").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
    state.backendSupportsTest = data.supports_send_test === true;
    byId("test-stale").hidden = state.backendSupportsTest;
    if (!state.testDefaultsSet) {
      const format = byId("test-format");
      const content = byId("test-content");
      if (Array.from(format.options).some((option) => option.value === data.send_format)) {
        format.value = data.send_format;
      }
      if (Array.from(content.options).some((option) => option.value === data.content_format)) {
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
      empty.colSpan = 6;
      empty.className = "empty-row";
      row.append(empty);
      body.append(row);
    }
    for (const item of state.subscriptions) {
      const row = document.createElement("tr");
      row.append(cell(item.name || "（未命名）"));
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
      const edit = button(
        "编辑",
        "nb-quiet",
        () => {
          startEdit(item);
        },
        true,
      );
      const remove = button("删除", "nb-danger", () => {}, true);
      armDelete(remove, () => {
        run(async () => {
          await call("subscriptions.remove", { id: item.id });
          notify("订阅已删除", `「${item.name}」的文章与已发送记录已一并删除。`, "ok");
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
    const previous = current === null || current === undefined ? select.value : String(current);
    const values = new Set(weights.map((value) => Number(value)));
    if (current !== null && current !== undefined) values.add(Number(current));
    const options = [new Option("自动（最小可用权重）", "")];
    for (const value of Array.from(values).sort((left, right) => left - right)) {
      options.push(new Option(`权重 ${value}`, String(value)));
    }
    select.replaceChildren(...options);
    if (previous && Array.from(select.options).some((option) => option.value === previous)) {
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
    byId("sub-url").value = "";
    byId("sub-weight").value = "";
    renderWeightOptions(state.availableWeights, null);
    updateControls();
  }

  function renderDestinations(data) {
    state.destinations = Array.isArray(data.items) ? data.items : [];
    byId("destination-count").textContent = `${state.destinations.length} 个${
      data.send_enabled ? "" : " · 定期发送未开启"
    }`;
    byId("destination-empty").hidden = state.destinations.length > 0;
    const body = byId("destination-rows");
    clear(body);
    if (!state.destinations.length) {
      const row = document.createElement("tr");
      const empty = cell("暂无接收者");
      empty.colSpan = 6;
      empty.className = "empty-row";
      row.append(empty);
      body.append(row);
    }
    for (const item of state.destinations) {
      const row = document.createElement("tr");
      row.append(cell(String(item.destination_id)));
      const status = document.createElement("span");
      status.className = `tag ${item.enabled ? "tag-ok" : "tag-bad"}`;
      status.textContent = item.enabled ? "启用" : "已暂停";
      row.append(cell(status));
      row.append(cell(formatTime(item.last_sent)));
      row.append(cell(formatTime(item.next_send)));
      const reason = document.createElement("span");
      reason.className = "reason-cell";
      reason.textContent = item.reason || "—";
      reason.title = item.reason || "";
      row.append(cell(reason, "reason-cell"));
      const toggle = button(
        item.enabled ? "暂停" : "继续",
        item.enabled ? "nb-quiet" : "nb-primary",
        () => {
          run(async () => {
            const result = await call("destinations.update", {
              qq: String(item.destination_id),
              enabled: !item.enabled,
            });
            notify(
              "接收者已更新",
              result.item.enabled
                ? `QQ ${item.destination_id} 将继续发送，失败原因已清空。`
                : `QQ ${item.destination_id} 已暂停发送。`,
              "ok",
            );
            await loadDestinations();
            await loadStatus();
          });
        },
        true,
      );
      const remove = button("删除", "nb-danger", () => {}, true);
      armDelete(remove, () => {
        run(async () => {
          await call("destinations.remove", { qq: String(item.destination_id) });
          notify("接收者已删除", `QQ ${item.destination_id} 的已发送记录已一并删除。`, "ok");
          await loadDestinations();
          await loadStatus();
        });
      });
      row.append(actionsCell(toggle, remove));
      body.append(row);
    }
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
      const parts = [item.source_name || `订阅 ${item.source_id}`, formatClock(item.pubdate)];
      if (!item.has_body) parts.push("无正文");
      if (item.deleted) parts.push("已标记清理");
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

  async function loadDestinations() {
    const data = await call("destinations.list");
    renderDestinations(data);
  }

  async function loadArticles() {
    const data = await call("articles.list", { limit: 20 });
    renderArticles(data);
  }

  async function loadAll() {
    await Promise.all([
      loadStatus(),
      loadSubscriptions(),
      loadDestinations(),
      loadArticles(),
    ]);
  }

  function renderPlaceholders() {
    const values = [
      "status-send",
      "status-cycle",
      "status-next",
      "status-pull",
      "status-articles",
      "status-latest-pull",
      "status-counts",
      "status-running",
    ];
    values.forEach((id) => {
      byId(id).textContent = "—";
    });
    byId("status-updated").textContent = "";
    setWarning("");
    byId("subscription-count").textContent = "—";
    byId("destination-count").textContent = "—";
    byId("subscription-empty").hidden = true;
    byId("destination-empty").hidden = true;
    byId("article-empty").hidden = true;
    clear(byId("subscription-rows"));
    clear(byId("destination-rows"));
    clear(byId("article-list"));
    byId("send-test").hidden = true;
    byId("unavailable").hidden = live();
    updateControls();
  }

  function submitSubscription(event) {
    event.preventDefault();
    if (!canManage()) return;
    const name = byId("sub-name").value.trim();
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
    const payload = { name, url };
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
          ? "URL 已修改，旧文章缓存与已发送记录已清空。"
          : "插件会在下一轮拉取时获取该源的文章。",
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

  function submitDestination(event) {
    event.preventDefault();
    if (!canManage()) return;
    const qq = byId("dest-qq").value.trim();
    if (!/^[1-9][0-9]{4,12}$/.test(qq)) {
      notify("QQ 号格式不正确", "请输入 5-13 位、不以 0 开头的数字 QQ 号。", "warn");
      byId("dest-qq").focus();
      return;
    }
    run(async () => {
      await call("destinations.create", { qq });
      notify("接收者已添加", `QQ ${qq} 会在下次发送窗口接收文章。`, "ok");
      byId("dest-qq").value = "";
      await loadDestinations();
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
    const sendFormat = byId("test-format").value;
    const contentFormat = byId("test-content").value;
    run(async () => {
      const result = await call("system.send_test", {
        qq,
        send_format: sendFormat,
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
    byId("pull-now").addEventListener("click", () => {
      run(async () => {
        const result = await call("system.pull_now");
        if (result.started) {
          notify("已开始拉取", "拉取在后台进行，完成后可刷新查看缓存文章。", "info");
          window.setTimeout(() => {
            if (live() && !state.busy) run(loadStatus, { quiet: true });
          }, 4000);
        } else {
          notify("已有拉取在进行", "请等待当前拉取完成后再试。", "warn");
        }
      });
    });
    byId("load-articles").addEventListener("click", () => {
      run(loadArticles);
    });
    byId("test-open").addEventListener("click", openSendTest);
    byId("test-cancel").addEventListener("click", closeSendTest);
    byId("send-test-form").addEventListener("submit", submitSendTest);
    byId("subscription-form").addEventListener("submit", submitSubscription);
    byId("destination-form").addEventListener("submit", submitDestination);
    byId("sub-cancel").addEventListener("click", resetSubscriptionForm);
  }

  function registerAnchors() {
    for (const id of [
      "status",
      "subscriptions",
      "subscription-form",
      "destinations",
      "destination-form",
      "send-test",
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
      state.initialized = true;
      byId("unavailable").hidden = true;
      await run(loadAll);
    } else {
      renderPlaceholders();
    }
    startPolling();
  }

  bootstrap();
})();
