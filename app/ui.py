from __future__ import annotations

from html import escape


PAGE_INFO = {
    "/": ("模拟账户", "账户总览"),
    "/accounts": ("账户详情", "账户"),
    "/operations": ("事务助手", "会话"),
    "/bills": ("账单", "账单"),
    "/subscriptions": ("订阅", "订阅"),
    "/results": ("操作状态", "操作状态"),
}


def render_page(path: str) -> str:
    title, nav_title = PAGE_INFO[path]
    route = path.strip("/") or "home"
    if path == "/accounts":
        content = '<section id="account-list" class="stack"></section><p class="muted">仅展示当前模拟会话中可核实的账户信息。流水与分类明细请查看账单页。</p><a class="text-link" href="/bills">查看模拟账单 →</a><p><a class="text-link" href="/operations">返回事务助手 →</a></p>'
    elif path == "/operations":
        content = """
<section class="status-card" id="scenario-panel" aria-live="polite"><h2>模拟资金时间线</h2><p>当前模拟日期：<strong id="scenario-date">读取中</strong></p><div id="scenario-events" class="stack"></div><button id="scenario-advance" type="button">推进到下一模拟日期</button><p id="scenario-message" class="muted" aria-live="polite"></p></section>
<section class="status-card" id="task-panel" aria-live="polite"><h2>跨场景任务链</h2><p id="task-status" class="muted">输入完整示范目标，在线模型会先提出计划，由服务端复核后展示步骤。</p><div id="task-steps" class="stack"></div><div id="task-controls" class="safety-flow"></div></section>
<section class="welcome" id="welcome">
  <p class="eyebrow">规则解析 · 跨场景目标使用 DeepSeek 在线草案</p>
  <h2>今天想处理什么？</h2>
  <p class="muted">查询模拟账单、查看订阅、准备模拟转账，或输入完整的跨场景目标。模型不可用时不会启动任务。</p><p class="sr-only">云盘疑似依据：至少 90 天未登录。</p>
  <div class="suggestions">
    <button type="button" data-suggestion="看看我 10 月的钱花在哪里，云盘不用了就帮我取消。等 11 月 5 日工资到账后还清信用卡，再给妈妈转 2,000 元生活费，最后告诉我还剩多少。"><span>二期任务链</span><strong>账单 → 订阅 → 工资 → 还款 → 转账</strong><b aria-hidden="true">→</b></button>
    <button type="button" data-suggestion="10 月我花在哪些地方？"><span>账单分析</span><strong>10 月我花在哪些地方？</strong><b aria-hidden="true">→</b></button>
    <button type="button" data-suggestion="云盘是不是还在扣钱？帮我取消"><span>订阅管理</span><strong>云盘是不是还在扣钱？帮我取消</strong><b aria-hidden="true">→</b></button>
    <button type="button" data-suggestion="给妈妈转 2000 元生活费"><span>模拟转账</span><strong>给妈妈转 2000 元生活费</strong><b aria-hidden="true">→</b></button>
  </div>
  <button class="quiet-button" id="replay-example" type="button">点选固定回放示例 <span aria-hidden="true">ⓘ</span></button>
</section>
<section id="conversation-messages" class="message-list" aria-live="polite"></section>
<div id="transfer-flow" class="result-flow"></div><div id="receipt" class="result-card" aria-live="polite"></div>
<form id="transfer" class="composer">
  <label for="message-input" class="sr-only">输入请求</label>
  <textarea id="message-input" name="text" rows="1" maxlength="300" placeholder="输入你想处理的事务…" required></textarea>
  <button class="send" type="submit" aria-label="解析请求">↑</button>
</form>
<p class="composer-note">仅处理虚构模拟操作 · 不连接真实金融服务</p>
<details class="advanced"><summary>查询已有模拟操作</summary><form id="session-status-query"><label for="session-status-input">操作 ID 或自然语言查状态</label><div class="form-row"><input id="session-status-input" name="query" placeholder="例如：最近一次操作状态如何？" required><button>查询</button></div></form><div id="session-status" class="result-card" aria-live="polite"></div></details>
<button id="reset-session" class="quiet-button" type="button">重置当前模拟会话</button><p id="reset-status" class="muted" aria-live="polite"></p>"""
    elif path == "/bills":
        content = '<p class="muted">月度流水与分类统计 · 数据来自当前模拟会话。</p><section id="bill-summary" class="stack" aria-live="polite"></section>'
    elif path == "/subscriptions":
        content = '<p class="muted">识别依据仅作提示；订阅状态以模拟回执为准。</p><section id="subscription-list" class="stack" aria-live="polite"></section><div id="subscription-receipt" class="result-card" aria-live="polite"></div>'
    elif path == "/results":
        content = """
<p class="muted">查看可核验的模拟操作回执。未知结果会保留为待核实。</p>
<form id="result-query" class="search-card"><label for="operation-id">按操作 ID 查询</label><div class="form-row"><input id="operation-id" name="operation_id" placeholder="粘贴操作 ID" required><button>查询</button></div></form>
<form id="natural-query" class="search-card"><label for="natural-status">自然语言查询</label><div class="form-row"><input id="natural-status" name="query" placeholder="例如：最近一次操作状态如何？" required><button>查询</button></div></form>
<div id="receipt" class="result-card" aria-live="polite"></div>"""
    else:
        content = """
<section class="balance-panel"><p class="eyebrow">DAILY ACCOUNT · 模拟余额</p><strong id="primary-balance" class="balance">登录后显示</strong><p class="muted">虚构账户余额，不代表总资产</p><a class="text-link" href="/accounts">查看账户详情 <span aria-hidden="true">→</span></a></section>
<section class="quick-links" aria-label="快捷入口"><a href="/operations"><span>↗</span>转账助手</a><a href="/bills"><span>▤</span>账单分析</a><a href="/subscriptions"><span>◷</span>订阅管理</a></section>
<div class="section-heading"><div><p class="eyebrow">YOUR ACCOUNTS</p><h2>我的账户</h2></div><a href="/accounts" class="text-link">账户详情</a></div><div class="account-switch" role="group" aria-label="选择账户类型"><button type="button" data-account-type="deposit" aria-pressed="true">借记卡</button><button type="button" data-account-type="credit_card" aria-pressed="false">信用卡</button></div><section id="home-accounts" class="stack"></section>
<a class="assistant-link" href="/operations"><span class="assistant-avatar">知</span><span><strong>问问日常银行事务助手</strong><small>从账单、订阅或转账开始</small></span><b aria-hidden="true">→</b></a>"""
    title = escape(title)
    nav_items = [
        ("/", "账户", "⌂"),
        ("/operations", "会话", "✳"),
        ("/bills", "账单", "▤"),
        ("/subscriptions", "订阅", "◷"),
    ]
    nav_items_html = []
    for href, label, icon in nav_items:
        active = href == path or (path == "/accounts" and href == "/")
        current = ' aria-current="page"' if active else ""
        if href == "/operations":
            nav_items_html.append(
                f'<a href="{href}"{current}>会话<span class="nav-icon" aria-hidden="true">{icon}</span><span class="assistant-label">助手</span></a>'
            )
        else:
            nav_items_html.append(
                f'<a href="{href}"{current}><span class="nav-icon" aria-hidden="true">{icon}</span><span>{label}</span></a>'
            )
    nav = "".join(nav_items_html)
    return TEMPLATE.replace("__TITLE__", title).replace("__ROUTE__", escape(route)).replace("__CONTENT__", content).replace("__NAV__", nav)


TEMPLATE = """<!doctype html>
<html lang="zh-Hans" data-theme="dark"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content">
<title>__TITLE__ · 日常银行事务助手</title>
<style>
:root {
  color-scheme: dark;
  --color-brand: #0A1F44; --color-canvas: #080D16; --color-surface: #131C2A; --color-surface-raised: #1B293D;
  --color-text: #F5F7FA; --color-text-muted: #AAB8CC; --color-border: #294265; --color-gold: #C9A86A;
  --color-gold-readable: #D9BD83; --color-success: #54C692; --color-warning: #F2AD62; --color-danger: #F07878;
  --color-on-brand: #F5F7FA; --opacity-disabled:.55; --shadow-card: 0 12px 32px rgba(0,0,0,.2); --shadow-nav: 0 -8px 28px rgba(0,0,0,.22);
  --radius-card: 16px; --radius-compact: 12px; --radius-pill: 999px; --transition-ui: 180ms ease;
}
:root[data-theme="light"] {
  color-scheme: light;
  --color-canvas: #F3F5F8; --color-surface: #FFFFFF; --color-surface-raised: #E8EDF4; --color-text: #17243A;
  --color-text-muted: #66758A; --color-border: #D8DFE9; --color-gold-readable: #806127;
  --color-success: #21865C; --color-warning: #A86113; --color-danger: #B83B43;
  --shadow-card: 0 12px 32px rgba(23,36,58,.08); --shadow-nav: 0 -8px 28px rgba(23,36,58,.1);
}
:root[data-theme="light"] .balance-panel { background:linear-gradient(145deg,var(--color-surface-raised),var(--color-surface) 78%); }
* { box-sizing: border-box; }
html { min-height: 100%; background: var(--color-canvas); scroll-padding-top: 64px; }
body { min-height: 100vh; margin: 0; background: var(--color-canvas); color: var(--color-text); font: 16px/1.55 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; font-synthesis: none; -webkit-font-smoothing: antialiased; }
button,input,textarea { font: inherit; color: inherit; }
a { color: inherit; text-decoration: none; }
button,a { -webkit-tap-highlight-color: transparent; }
.app { width: min(100%,480px); min-height: 100vh; margin: 0 auto; padding-bottom: calc(88px + env(safe-area-inset-bottom)); }
.demo-banner { position: sticky; top: 0; z-index: 20; padding: 7px 12px; background: var(--color-brand); color: var(--color-on-brand); text-align: center; font-size: 12px; letter-spacing: .03em; }
.topbar { display: flex; align-items: center; justify-content: space-between; padding: 14px 20px 9px; }
.wordmark { display: flex; align-items: center; gap: 9px; font-size: 14px; font-weight: 650; }
.brand-icon,.assistant-avatar { display: grid; flex: 0 0 auto; place-items: center; width: 34px; height: 34px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface); color: var(--color-gold-readable); font-weight: 700; }
.top-actions { display: flex; gap: 8px; }
.icon-button { display: grid; place-items: center; width: 44px; height: 44px; border: 1px solid var(--color-border); border-radius: var(--radius-pill); background: var(--color-surface); cursor: pointer; }
.page-head { padding: 10px 20px 16px; }
.page-head h1 { margin: 0; font-size: 24px; line-height: 1.25; letter-spacing: -.025em; font-weight: 650; }
.eyebrow { margin: 0 0 5px; color: var(--color-text-muted); font-size: 12px; letter-spacing: .08em; }
.muted { color: var(--color-text-muted); font-size: 14px; }
main { padding: 0 20px 24px; animation: fade-in 200ms ease both; }
.section-heading { display:flex; align-items:center; justify-content:space-between; margin:22px 0 10px; }
.section-heading h2 { margin:0; font-size:18px; }
.text-link { color:var(--color-gold-readable); font-size:14px; font-weight:600; }
.balance-panel { padding:22px 20px; border:1px solid var(--color-border); border-radius:var(--radius-card); background:linear-gradient(145deg,var(--color-brand),var(--color-surface) 78%); box-shadow:var(--shadow-card); }
.balance { display:block; margin:9px 0 2px; font-size:38px; line-height:1.1; letter-spacing:-.04em; font-weight:650; font-variant-numeric:tabular-nums; }
.balance-panel .muted { margin:0 0 15px; font-size:13px; }
.quick-links { display:grid; grid-template-columns:repeat(3,1fr); gap:9px; margin:16px 0 20px; }
.quick-links a { display:grid; justify-items:center; gap:3px; min-height:76px; padding:10px 4px; border:1px solid var(--color-border); border-radius:var(--radius-card); background:var(--color-surface); font-size:13px; }
.quick-links a span { color:var(--color-gold-readable); font-size:19px; }
.stack { display:grid; gap:10px; }
.account-switch { display:grid; grid-template-columns:repeat(2,1fr); gap:6px; margin:10px 0; padding:4px; border-radius:var(--radius-pill); background:var(--color-surface-raised); }
.account-switch button { border-color:transparent; background:transparent; }
.account-switch button[aria-pressed="true"] { background:var(--color-surface); color:var(--color-gold-readable); box-shadow:var(--shadow-card); }
.account-card,.subscription-card,.bill-card,.status-card,.search-card { border:1px solid var(--color-border); border-radius:var(--radius-card); background:var(--color-surface); box-shadow:var(--shadow-card); }
.account-card { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:3px 12px; padding:15px; }
.account-card strong:first-child { min-width:0; }
.account-card .account-type { color:var(--color-text-muted); font-size:12px; }
.account-balance { grid-column:2; grid-row:1/3; font-size:19px; font-weight:650; font-variant-numeric:tabular-nums; white-space:nowrap; }
.assistant-link { display:flex; align-items:center; gap:12px; margin-top:20px; padding:14px; border:1px solid var(--color-border); border-radius:var(--radius-card); background:var(--color-surface); }
.assistant-avatar { background:var(--color-brand); }
.assistant-link strong,.assistant-link small { display:block; }
.assistant-link strong { font-size:14px; }
.assistant-link small { margin-top:2px; color:var(--color-text-muted); font-size:12px; }
.assistant-link b { margin-left:auto; color:var(--color-text-muted); font-size:20px; }
.welcome { padding:18px 0 20px; text-align:center; }
.welcome h2 { margin:8px 0; font-size:28px; line-height:1.25; letter-spacing:-.03em; }
.welcome>p:not(.eyebrow) { max-width:340px; margin:0 auto; }
.suggestions { display:grid; gap:9px; margin:20px 0 8px; text-align:left; }
.suggestions button { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:2px 10px; min-height:64px; padding:10px 14px; border:1px solid var(--color-border); border-radius:var(--radius-card); background:var(--color-surface); text-align:left; cursor:pointer; transition:background var(--transition-ui),transform var(--transition-ui); }
.suggestions button:hover { background:var(--color-surface-raised); transform:translateY(-1px); }
.suggestions button span { color:var(--color-text-muted); font-size:11px; }
.suggestions button strong { font-size:14px; font-weight:550; }
.suggestions button b { grid-column:2; grid-row:1/3; color:var(--color-text-muted); font-size:19px; }
button { min-width:44px; min-height:44px; padding:8px 14px; border:1px solid var(--color-border); border-radius:var(--radius-pill); background:var(--color-surface-raised); cursor:pointer; transition:background var(--transition-ui); }
button:hover { background:var(--color-border); }
button:disabled { opacity:var(--opacity-disabled); cursor:wait; }
button:focus-visible,a:focus-visible,input:focus-visible,textarea:focus-visible,summary:focus-visible { outline:3px solid var(--color-gold-readable); outline-offset:3px; }
.quiet-button { border-color:transparent; background:transparent; color:var(--color-text-muted); font-size:12px; }
.composer { position:sticky; bottom:calc(74px + env(safe-area-inset-bottom)); z-index:5; display:flex; align-items:flex-end; gap:8px; margin:16px -4px 0; padding:8px 8px 8px 14px; border:1px solid var(--color-border); border-radius:24px; background:var(--color-surface); box-shadow:var(--shadow-card); }
.composer textarea { width:100%; min-height:42px; max-height:112px; resize:vertical; padding:9px 0; border:0; outline:0; background:transparent; }
.composer textarea::placeholder,input::placeholder { color:var(--color-text-muted); }
.send,.primary { border:0; background:var(--color-gold); color:var(--color-brand); font-weight:700; }
.send { flex:0 0 44px; width:44px; height:44px; padding:0; font-size:22px; }
.composer-note { margin:6px 0; text-align:center; color:var(--color-text-muted); font-size:11px; }
.message-list { display:grid; gap:12px; }
.message { max-width:90%; padding:11px 14px; border-radius:16px; background:var(--color-surface-raised); overflow-wrap:anywhere; }
.message.user { justify-self:end; border-bottom-right-radius:5px; background:var(--color-brand); color:var(--color-on-brand); }
.message.assistant { justify-self:start; border-bottom-left-radius:5px; }
.message small { display:block; margin-top:5px; color:var(--color-text-muted); font-size:11px; }
.result-flow { display:grid; gap:10px; }
.result-card,.status-card { margin-top:12px; padding:14px; border:1px solid var(--color-border); border-radius:var(--radius-card); background:var(--color-surface); overflow-wrap:anywhere; word-break:break-word; }
.result-card:empty { display:none; }
.result-card p,.status-card p { margin:6px 0; }
.status-card h2,.subscription-card h2,.bill-card h2 { margin:0 0 5px; font-size:16px; }
.result-status { display:inline-flex; padding:4px 10px; border-radius:var(--radius-pill); background:var(--color-surface-raised); color:var(--color-text-muted); font-size:12px; }
.result-status.success { color:var(--color-success); }
.result-status.warning { color:var(--color-warning); }
.result-status.danger { color:var(--color-danger); }
.primary { min-height:46px; }
.safety-flow { display:grid; gap:9px; margin-top:12px; }
.safety-flow input,.form-row input { min-width:0; width:100%; }
.safety-flow label { font-size:13px; }
.safety-flow .primary { width:100%; }
.advanced { margin:18px 0; padding:11px 13px; border:1px solid var(--color-border); border-radius:var(--radius-compact); color:var(--color-text-muted); font-size:13px; }
.advanced summary { min-height:34px; padding:4px 0; color:var(--color-text); cursor:pointer; }
.advanced form { display:grid; gap:8px; }
.search-card { display:grid; gap:8px; margin:12px 0; padding:14px; }
.search-card label,.advanced label { font-size:13px; font-weight:600; }
.form-row { display:flex; gap:8px; }
.form-row input { flex:1; }
input { min-height:44px; padding:9px 13px; border:1px solid var(--color-border); border-radius:var(--radius-pill); background:var(--color-canvas); color:var(--color-text); }
#access-panel { margin:0 20px 18px; padding:12px; border:1px solid var(--color-border); border-radius:var(--radius-card); background:var(--color-surface); }
#access-panel form { display:flex; gap:8px; }
#access-panel input { min-width:0; flex:1; }
#access-panel button { background:var(--color-gold); color:var(--color-brand); font-weight:650; }
#error { margin:5px 0 0; color:var(--color-danger); font-size:13px; }
#access-panel[hidden] { display:none; }
.metric-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:9px; margin:12px 0; }
.metric { min-width:0; padding:11px; border-radius:var(--radius-compact); background:var(--color-surface-raised); }
.metric small,.metric strong { display:block; }
.metric small { color:var(--color-text-muted); font-size:12px; }
.metric strong { margin-top:3px; font-size:17px; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
.category-row { display:grid; grid-template-columns:minmax(65px,auto) minmax(20px,1fr) auto; align-items:center; gap:8px; margin:10px 0; font-size:12px; }
.category-track { height:7px; overflow:hidden; border-radius:var(--radius-pill); background:var(--color-surface-raised); }
.category-fill { height:100%; border-radius:inherit; background:var(--color-gold); }
.transaction-row { display:flex; justify-content:space-between; gap:12px; padding:11px 0; border-bottom:1px solid var(--color-border); font-size:13px; }
.transaction-row:last-child { border-bottom:0; }
.transaction-row span:first-child { min-width:0; overflow-wrap:anywhere; }
.transaction-row small { display:block; color:var(--color-text-muted); }
.subscription-card,.bill-card { padding:15px; }
.subscription-card p,.bill-card p { margin:7px 0; font-size:14px; }
.subscription-card button,.bill-card button { margin-top:8px; background:var(--color-gold); color:var(--color-brand); font-weight:650; }
.status-pill { display:inline-flex; padding:4px 9px; border-radius:var(--radius-pill); background:var(--color-surface-raised); color:var(--color-text-muted); font-size:12px; }
.status-pill.success { color:var(--color-success); }
.month-chip { display:inline-flex; padding:6px 11px; border:1px solid var(--color-border); border-radius:var(--radius-pill); color:var(--color-text-muted); font-size:12px; }
#reset-session { margin-top:18px; }
.safety-active .welcome,.safety-active .composer,.safety-active .composer-note,.safety-active .advanced,.safety-active #reset-session,.safety-active .app-nav { display:none; }
.app-nav { position:fixed; left:50%; bottom:0; z-index:10; display:grid; grid-template-columns:repeat(4,1fr); gap:4px; width:min(100%,480px); padding:8px 10px calc(8px + env(safe-area-inset-bottom)); transform:translateX(-50%); border-top:1px solid var(--color-border); background:var(--color-surface); box-shadow:var(--shadow-nav); }
.app-nav a { display:flex; min-width:44px; min-height:50px; flex-direction:column; align-items:center; justify-content:center; gap:2px; border-radius:var(--radius-pill); color:var(--color-text-muted); font-size:11px; line-height:1.2; }
.app-nav a[aria-current="page"] { background:var(--color-surface-raised); color:var(--color-gold-readable); }
.nav-icon { font-size:18px; line-height:1.1; }
.app-nav a[href="/operations"] { font-size:0; }
.app-nav a[href="/operations"] .assistant-label { font-size:11px; }
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
@keyframes fade-in { from { opacity:0; transform:translateY(5px); } to { opacity:1; transform:translateY(0); } }
@media (min-width:760px) { body { padding:24px 0; } .app { min-height:calc(100vh - 48px); border:1px solid var(--color-border); border-radius:24px; overflow:clip; box-shadow:var(--shadow-card); } .app-nav { bottom:24px; border:1px solid var(--color-border); border-radius:22px; } .demo-banner { border-radius:23px 23px 0 0; } }
@media (max-width:360px) { .topbar,.page-head { padding-left:14px; padding-right:14px; } main { padding-right:14px; padding-left:14px; } }
@media (prefers-reduced-motion:reduce) { *,*::before,*::after { scroll-behavior:auto!important; animation-duration:.01ms!important; transition-duration:.01ms!important; } }
</style></head>
<body><div class="app" data-page="__ROUTE__">
<div class="demo-banner">模拟环境 · 虚构数据 <span aria-hidden="true">·</span> 仅供功能演示</div>
<header class="topbar"><a href="/" class="wordmark" aria-label="日常银行事务助手首页"><span class="brand-icon" aria-hidden="true">知</span><span>日常银行事务助手</span></a><div class="top-actions"><button id="theme-toggle" class="icon-button" type="button" aria-label="切换浅色主题" title="切换深浅色">☼</button></div></header>
<div class="page-head"><p class="eyebrow">PERSONAL · DEMO</p><h1>__TITLE__</h1></div>
<section id="access-panel"><form id="access"><label class="sr-only" for="access-code">访问码</label><input id="access-code" name="access_code" type="password" autocomplete="off" placeholder="输入演示访问码" required><button>进入</button></form><p id="error" role="alert"></p></section>
<main>__CONTENT__</main><nav class="app-nav" aria-label="主导航">__NAV__</nav></div>
<script>
const page = document.querySelector('.app').dataset.page;
const accessPanel = document.querySelector('#access-panel');
const themeButton = document.querySelector('#theme-toggle');
const root = document.documentElement;
function applyTheme(theme) { root.dataset.theme = theme; themeButton.textContent = theme === 'dark' ? '☼' : '☾'; themeButton.setAttribute('aria-label', theme === 'dark' ? '切换浅色主题' : '切换深色主题'); }
try { applyTheme(sessionStorage.getItem('t12-theme') || 'dark'); } catch { applyTheme('dark'); }
themeButton.addEventListener('click', () => { const theme = root.dataset.theme === 'dark' ? 'light' : 'dark'; applyTheme(theme); try { sessionStorage.setItem('t12-theme', theme); } catch {} });
document.querySelectorAll('.app-nav a').forEach(link => { if (link.getAttribute('href') === location.pathname || (location.pathname === '/accounts' && link.getAttribute('href') === '/')) link.setAttribute('aria-current','page'); });
document.querySelector('#access').addEventListener('submit', async event => { event.preventDefault(); const form = event.currentTarget; const access_code = new FormData(form).get('access_code'); const response = await fetch('/api/v1/access/session', {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({access_code})}); const result = await response.json(); document.querySelector('#error').textContent = response.ok ? '' : (result.detail?.message || result.message || '访问码无效，请重试。'); if (response.ok) { form.reset(); await loadSession(); } });
const money = cents => '¥' + (cents / 100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
async function loadSession() { const response = await fetch('/api/v1/session'); if (!response.ok) { accessPanel.hidden = false; return; } accessPanel.hidden = true; const accountsResponse = await fetch('/api/v1/accounts'); if (!accountsResponse.ok) { accessPanel.hidden = false; clearConversation(); return; } const accounts = (await accountsResponse.json()).accounts; renderAccounts(accounts); if (page === 'operations') { await loadScenario(); await loadTaskPanel(); } if (page === 'bills') await loadBill(); if (page === 'subscriptions') await loadTraditionalSubscriptions(); }
let selectedAccountType = 'deposit';
function accountCard(account) { const card = document.createElement('article'); card.className = 'account-card'; const name = document.createElement('strong'); name.textContent = account.name; const type = document.createElement('span'); type.className = 'account-type'; type.textContent = account.account_type === 'deposit' ? '借记账户 · 虚构数据' : '信用账户 · 虚构数据'; const balance = document.createElement('strong'); balance.className = 'account-balance'; balance.textContent = money(account.balance_cents); card.append(name,type,balance); return card; }
function renderAccounts(accounts) { const home = document.querySelector('#home-accounts'); const list = document.querySelector('#account-list'); const primary = document.querySelector('#primary-balance'); const debit = accounts.find(account => account.account_type === 'deposit') || accounts[0]; if (primary && debit) primary.textContent = money(debit.balance_cents); if (home) home.replaceChildren(); if (list) list.replaceChildren(); accounts.forEach(account => { const card = accountCard(account); if (list) list.append(card); }); if (home) { const tabs = document.querySelectorAll('[data-account-type]'); const selectAccount = type => { selectedAccountType = type; const selected = accounts.find(account => account.account_type === type) || debit; home.replaceChildren(accountCard(selected)); tabs.forEach(tab => tab.setAttribute('aria-pressed', String(tab.dataset.accountType === selected.account_type))); }; tabs.forEach(tab => tab.addEventListener('click', () => selectAccount(tab.dataset.accountType))); selectAccount(selectedAccountType); } }
async function loadScenario() { const date=document.querySelector('#scenario-date');const list=document.querySelector('#scenario-events');if(!date||!list)return;const response=await fetch('/api/v1/scenario');const result=await response.json();if(!response.ok){date.textContent='无法读取';return;}date.textContent=result.scenario_date;list.replaceChildren();const labels={NOV001:'工资入账',NOV002:'归还10月信用卡账单（NOV002/NOV003原子应用）',NOV003:'与NOV002同笔还款',NOV004:'母亲生活费转账（需验证码与同参确认）',NOV005:'团建费用报销'};result.events.forEach(item=>{const card=document.createElement('article');card.className='account-card';const label=document.createElement('strong');label.textContent=labels[item.source_entry_id]+' · '+item.source_entry_id;const detail=document.createElement('span');detail.className='account-type';detail.textContent=item.posting_date+' · '+money(item.amount_cents)+' · '+(item.status==='applied'?'已应用':item.status==='not_applied'?'未应用':item.status);card.append(label,detail);if(item.operation_id){const op=document.createElement('small');op.textContent='操作 ID：'+item.operation_id;card.append(op);}if(['NOV001','NOV005'].includes(item.source_entry_id)&&item.status==='not_applied'&&item.posting_date<=result.scenario_date){const button=document.createElement('button');button.type='button';button.textContent='应用'+labels[item.source_entry_id];button.addEventListener('click',async()=>{button.disabled=true;const applied=await fetch('/api/v1/scenario/events/'+item.source_entry_id+'/apply',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()}});const body=await applied.json();document.querySelector('#scenario-message').textContent=applied.ok?'已应用并记录回执：'+body.receipt.operation_id:(body.detail?.code==='SCENARIO_EVENTS_INCOMPLETE'?'请先应用前序日期事件。':'事件未应用：'+(body.detail?.code||'状态未知'));await loadSession();});card.append(button);}if(['NOV002','NOV003'].includes(item.source_entry_id)&&item.status==='not_applied'){const note=document.createElement('small');note.textContent='还款需独立模拟验证并确认同一参数；请通过任务链发起。';card.append(note);}list.append(card);});const advance=document.querySelector('#scenario-advance');if(advance){advance.textContent=result.scenario_date==='2026-10-31'?'进入 2026-11-05':result.scenario_date==='2026-11-05'?'进入 2026-11-10':'场景日期已到终点';advance.disabled=result.scenario_date==='2026-11-10';}}
const scenarioAdvance=document.querySelector('#scenario-advance');if(scenarioAdvance)scenarioAdvance.addEventListener('click',async()=>{scenarioAdvance.disabled=true;const response=await fetch('/api/v1/scenario/advance',{method:'POST'});const result=await response.json();const message=document.querySelector('#scenario-message');message.textContent=response.ok?'模拟日期已推进至 '+result.scenario_date:result.detail?.code==='SCENARIO_EVENTS_INCOMPLETE'?'请先完成当前日期的冻结事件。':'模拟日期未推进。';await loadSession();});
function cardElement(className,title) { const card = document.createElement('article'); card.className = className; if (title) { const heading = document.createElement('h2'); heading.textContent = title; card.append(heading); } return card; }
async function loadBill() { const panel = document.querySelector('#bill-summary'); if (!panel) return; const response = await fetch('/api/v1/bills/2026-10'); const result = await response.json(); panel.replaceChildren(); if (!response.ok) { panel.textContent = '仅提供 2026 年 10 月模拟账单。'; return; } const summary = cardElement('bill-card','2026 年 10 月账单概览'); const metrics = document.createElement('div'); metrics.className = 'metric-grid'; [[money(result.monthly_spend_cents),'月支出'],[result.transaction_count+' 笔','当月流水'],[result.spend_transaction_count+' 笔','计入消费']].forEach(([value,label]) => { const item=document.createElement('div'); item.className='metric'; const amount=document.createElement('strong'); amount.textContent=value; const caption=document.createElement('small'); caption.textContent=label; item.append(amount,caption); metrics.append(item); }); summary.append(metrics); const max=Math.max(...result.categories.map(item=>item.amount_cents),1); result.categories.forEach(item=>{const row=document.createElement('div');row.className='category-row';const name=document.createElement('span');name.textContent=item.category;const track=document.createElement('span');track.className='category-track';const fill=document.createElement('span');fill.className='category-fill';fill.style.width=(item.amount_cents/max*100)+'%';track.append(fill);const amount=document.createElement('span');amount.textContent=money(item.amount_cents);row.append(name,track,amount);summary.append(row);}); panel.append(summary); result.anomalies.forEach(item=>{const card=cardElement('bill-card',item.label);const transaction=document.createElement('p');transaction.textContent=item.transaction.posting_date+' · '+item.transaction.description+' · '+money(item.transaction.amount_cents)+' · '+item.transaction.category;card.append(transaction);if(item.transaction.source_entry_id==='TX002'){if(item.transaction.new_category){const change=document.createElement('p');change.textContent='分类更正：'+item.transaction.original_category+' → '+item.transaction.new_category+'；修改时间：'+item.transaction.corrected_at;card.append(change);}else{const button=document.createElement('button');button.type='button';button.textContent='确认更正为团建垫付（待报销）';button.addEventListener('click',async()=>{if(!window.confirm('确认将团建 ¥3,180.00 从餐饮更正为“团建垫付（待报销）”吗？'))return;button.disabled=true;const corrected=await fetch('/api/v1/bills/corrections/TX002',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({confirmed:true})});if(corrected.ok)await loadBill();else document.querySelector('#error').textContent='分类更正未完成，请重新查询账单。';});card.append(button);}}const basis=document.createElement('p');basis.textContent='判断依据：'+item.basis;const uncertainty=document.createElement('p');uncertainty.textContent='不确定性：'+item.uncertainty;card.append(basis,uncertainty);panel.append(card);}); }
async function loadSubscriptions() { const panel=document.querySelector('#subscription-list'); if(!panel)return; const response=await fetch('/api/v1/subscriptions');const result=await response.json();panel.replaceChildren();if(!response.ok){panel.textContent='请重新进入模拟环境后查询订阅。';return;}result.subscriptions.forEach(item=>{const card=cardElement('subscription-card',item.merchant);const evidence=document.createElement('p');evidence.textContent=(item.source_id==='SUB-CLOUD'?'疑似依据 · ':'模拟订阅 · ')+money(item.amount_cents)+'/'+(item.period==='monthly'?'月':item.period)+(item.source_id==='SUB-CLOUD'?' · 连续扣款 '+item.consecutive_charges+' 个月 · 至少 '+item.days_since_last_use+' 天未登录；仅为疑似依据。':' · 扣款频次与最近使用时间未核实。');const status=document.createElement('span');status.className='status-pill '+(item.status==='cancelled'?'success':'');status.textContent='状态：'+(item.status==='cancelled'?'已取消':item.status==='active'?'在用':item.status);card.append(evidence,status);if(item.cancellation_receipt){const receipt=document.createElement('p');receipt.textContent='取消回执：'+item.cancellation_receipt.status+' · 操作 ID：'+item.cancellation_receipt.operation_id+' · 时间：'+item.cancellation_receipt.completed_at+' · 模拟环境';card.append(receipt);}if(item.source_id==='SUB-CLOUD'&&item.status==='active'){const button=document.createElement('button');button.type='button';button.textContent='确认取消模拟云盘订阅';button.addEventListener('click',async()=>{if(!window.confirm('仅取消虚构的模拟云盘订阅。此操作不代表退款、止付或真实商户取消。确认继续吗？'))return;button.disabled=true;const cancelled=await fetch('/api/v1/subscriptions/'+encodeURIComponent(item.id)+'/cancel',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({confirmed:true})});const body=await cancelled.json();const receiptPanel=document.querySelector('#subscription-receipt');receiptPanel.replaceChildren();const line=document.createElement('p');line.textContent=body.receipt?.status==='succeeded'?'已取消 · 操作 ID：'+body.receipt.operation_id+' · 时间：'+body.receipt.completed_at+' · 模拟环境 / 虚构数据':body.receipt?.message||'模拟取消失败或待核实；当前订阅状态未报告为已取消。';receiptPanel.append(line);await loadSubscriptions();});card.append(button);}panel.append(card);}); }
async function loadTraditionalSubscriptions() { await loadSubscriptions(); }
function addMessage(text,side) { const box=document.querySelector('#conversation-messages'); if(!box)return; const message=document.createElement('div');message.className='message '+side;message.textContent=text;box.append(message);message.scrollIntoView({block:'nearest',behavior:'smooth'}); }
function showReceipt(proof,target) { const panel=document.querySelector(target||'#receipt');if(!panel)return;panel.replaceChildren();const tone=proof.status==='succeeded'?'success':proof.status==='pending_verification'?'warning':'danger';const badge=document.createElement('span');badge.className='result-status '+tone;badge.textContent=proof.status==='succeeded'?'已完成':proof.status==='cancelled'?'已取消':proof.status==='pending_verification'?'待核实':proof.status==='failed'?'未成功':proof.status||'状态未知';panel.append(badge);const summary=proof.summary||{};const rows=[['操作类型',proof.operation_type||'未知操作'],['参数摘要',[summary.recipient,summary.amount_cents!=null?money(summary.amount_cents):null,summary.purpose,proof.merchant].filter(Boolean).join(' · ')||'无'],['模拟状态',proof.status||'状态未知'],['操作 ID',proof.operation_id||'无'],['时间',proof.completed_at||proof.created_at||'无'],['失败原因',proof.message||proof.error_code||'无']];rows.forEach(([label,value])=>{const line=document.createElement('p');const strong=document.createElement('strong');strong.textContent=label+'：';line.append(strong,document.createTextNode(value));panel.append(line);});const note=document.createElement('p');note.className='muted';note.textContent='模拟环境 · 虚构数据';panel.append(note);}
async function queryReceipt(url,target) { const response=await fetch(url);const result=await response.json();if(response.ok&&result.receipt)showReceipt(result.receipt,target);else{const panel=document.querySelector(target||'#receipt');if(panel){panel.textContent='未找到模拟操作回执。';panel.classList.add('muted');}}}
function extractOperationId(text) { const found=text.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);return found&&found[0]; }
async function parseIntent(text,mode) { const response=await fetch('/api/v1/intents/parse',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text:text,mode:mode||'rules'})});const result=await response.json();if(!response.ok){addMessage(result.detail?.message||'无法解析此请求。','assistant');return;}const modeLabel=result.mode==='fixed_replay'?'用户点选的固定回放（预置结果，不是实时模型理解）':'本次请求使用规则解析；跨场景任务另走在线模型';const intentText=result.message|| (result.intent==='transfer'?'已识别模拟转账，请先核对信息。':result.intent==='bill_query'?'我来查看 10 月模拟账单。':'已完成规则解析。');addMessage(intentText+'\\n'+modeLabel,'assistant');if(result.intent==='bill_query'){await renderBillResult();return;}if(result.intent==='subscription_query'||result.intent==='subscription_cancel'){await renderSubscriptionResult(result);return;}if(result.intent==='bill_correction'){addMessage('请前往账单页核对更正对象并确认。','assistant');return;}if(result.intent==='transfer'&&result.status==='ready_for_confirmation'){if(result.merged_from_context)askContinuePreparing(result.slots);else await prepareTransfer(result.slots);return;}if(result.intent==='transfer'&&result.candidates?.length){result.candidates.forEach(candidate=>{const button=document.createElement('button');button.textContent=candidate.name+' · '+candidate.relationship+' · 尾号 '+candidate.account_suffix;button.addEventListener('click',()=>askContinuePreparing({...result.slots,contact_id:candidate.contact_id}));document.querySelector('#transfer-flow').append(button);});return;}if(result.intent==='refusal'||result.intent==='out_of_scope'||result.intent==='unknown'){addMessage(result.message||'请补充说明。','assistant');}}
function askContinuePreparing(slots) { const flow=document.querySelector('#transfer-flow');flow.replaceChildren();const prompt=document.createElement('p');prompt.textContent='补充信息已合并，是否继续准备这笔转账？';const yes=document.createElement('button');yes.type='button';yes.textContent='继续准备';const no=document.createElement('button');no.type='button';no.textContent='取消';flow.append(prompt,yes,no);yes.addEventListener('click',()=>{yes.disabled=true;no.disabled=true;prepareTransfer(slots);});no.addEventListener('click',()=>{flow.replaceChildren();addMessage('已取消准备；当前会话状态未改变。','assistant');});}
async function renderBillResult(){const response=await fetch('/api/v1/bills/2026-10');const result=await response.json();if(!response.ok){addMessage('当前无法读取模拟账单，请稍后重试。','assistant');return;}const summary='10 月模拟账单：月支出 '+money(result.monthly_spend_cents)+'，'+result.transaction_count+' 笔流水、'+result.spend_transaction_count+' 笔计入消费。';addMessage(summary,'assistant');const link=document.createElement('a');link.className='text-link';link.href='/bills';link.textContent='打开账单详情 →';document.querySelector('#conversation-messages').append(link);}
async function renderSubscriptionResult(parsed){const response=await fetch('/api/v1/subscriptions');const result=await response.json();if(!response.ok){addMessage('当前无法读取模拟订阅，请稍后重试。','assistant');return;}const merchant=parsed.slots.merchant;const item=result.subscriptions.find(row=>row.merchant===merchant);if(!item){addMessage('当前会话没有找到对应的模拟订阅。','assistant');return;}const summary=item.merchant+' · '+money(item.amount_cents)+'/'+(item.period==='monthly'?'月':item.period)+' · 状态：'+(item.status==='active'?'在用':'已取消')+(item.source_id==='SUB-CLOUD'?' · 连续扣款 '+item.consecutive_charges+' 次 · 至少 '+item.days_since_last_use+' 天未登录（疑似依据）':' · 扣款频次与最近使用时间未核实');addMessage(summary,'assistant');const link=document.createElement('a');link.className='text-link';link.href='/subscriptions';link.textContent=parsed.intent==='subscription_cancel'?'前往订阅页确认取消 →':'查看订阅详情 →';document.querySelector('#conversation-messages').append(link);}
let transferIdempotencyKey=null;
async function prepareTransfer(slots){const flow=document.querySelector('#transfer-flow');flow.replaceChildren();const response=await fetch('/api/v1/operations/transfer/prepare',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({slots:slots,idempotency_key:transferIdempotencyKey||(transferIdempotencyKey=crypto.randomUUID())})});const prepared=await response.json();if(!response.ok){addMessage(prepared.detail?.message||prepared.detail?.code||'无法准备模拟转账。','assistant');return;}if(prepared.receipt){showReceipt(prepared.receipt);await loadSession();return;}if(prepared.candidates){prepared.candidates.forEach(candidate=>{const button=document.createElement('button');button.textContent=candidate.name+' · '+candidate.relationship+' · 尾号 '+candidate.account_suffix;button.addEventListener('click',()=>askContinuePreparing({...slots,contact_id:candidate.contact_id}));flow.append(button);});return;}const id=prepared.operation_id;if(!id)return;document.body.classList.add('safety-active');const card=cardElement('status-card','转账参数摘要');const summary=prepared.summary;[['收款人',summary.recipient+(summary.relationship?' · '+summary.relationship:'')],['金额',money(summary.amount_cents)],['来源账户',summary.source_account],['用途',summary.purpose],['风险等级',summary.risk_level==='RED'?'红色 · 需要强验证':summary.risk_level],['环境','仅使用虚构模拟资金']].forEach(([label,value])=>{const line=document.createElement('p');line.textContent=label+'：'+value;card.append(line);});flow.append(card);const controls=document.createElement('div');controls.className='safety-flow';let confirmButton;if(summary.risk_level==='RED'){const label=document.createElement('label');label.textContent='模拟验证码';const code=document.createElement('input');code.autocomplete='off';code.inputMode='numeric';code.placeholder='输入演示验证码';code.setAttribute('aria-label','模拟验证码');const verify=document.createElement('button');verify.type='button';verify.textContent='验证验证码';const feedback=document.createElement('p');feedback.className='muted';confirmButton=document.createElement('button');confirmButton.className='primary';confirmButton.type='button';confirmButton.textContent='确认相同参数并执行';confirmButton.disabled=true;controls.append(label,code,verify,feedback,confirmButton);verify.addEventListener('click',async()=>{verify.disabled=true;const checked=await fetch('/api/v1/operations/'+id+'/verify',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({code:code.value})});verify.disabled=false;if(checked.ok){confirmButton.disabled=false;feedback.textContent='验证通过。请确认上方同一组参数。';}else{const error=await checked.json();feedback.textContent=checked.status===423?'当前会话已锁定 15 分钟。':error.detail?.message||'验证码错误，请重试。';}});}else{confirmButton=document.createElement('button');confirmButton.className='primary';confirmButton.textContent='确认参数并执行';controls.append(confirmButton);}const cancel=document.createElement('button');cancel.type='button';cancel.textContent='取消返回';controls.append(cancel);flow.append(controls);cancel.addEventListener('click',async()=>{const result=await fetch('/api/v1/operations/'+id+'/cancel',{method:'POST'});if(result.ok){document.body.classList.remove('safety-active');cancel.disabled=true;confirmButton.disabled=true;flow.replaceChildren();addMessage('已取消模拟转账，没有执行。','assistant');transferIdempotencyKey=null;}});confirmButton.addEventListener('click',async()=>{confirmButton.disabled=true;const confirmed=await fetch('/api/v1/operations/'+id+'/confirm',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({summary_hash:prepared.summary_hash})});if(!confirmed.ok){document.body.classList.remove('safety-active');flow.replaceChildren();addMessage('参数摘要已变化，请重新确认。','assistant');return;}let result;try{const executed=await fetch('/api/v1/operations/'+id+'/execute',{method:'POST'});result=await executed.json();}catch{const queried=await fetch('/api/v1/operations/'+id);result=await queried.json();}document.body.classList.remove('safety-active');flow.replaceChildren();if(result.receipt){showReceipt(result.receipt);addMessage(result.receipt.status==='succeeded'?'模拟操作已完成；回执已生成。':'模拟操作状态：'+result.receipt.status+'。请以回执为准。','assistant');await loadSession();}else{addMessage('操作结果待核实，请查询原操作状态。','assistant');}});}
const fixedGoalTerms=['云盘','工资','还清信用卡','妈妈','还剩多少'];
function isFixedGoal(text){return fixedGoalTerms.every(term=>text.includes(term));}
async function taskRequest(path,body,headers){const options={method:'POST',headers:{...headers}};if(body!==null){options.headers['content-type']='application/json';options.body=JSON.stringify(body);}const response=await fetch(path,options);return {ok:response.ok,data:await response.json()};}
async function startFixedTask(text){const result=await taskRequest('/api/v1/tasks/start',{text});if(!result.ok){addMessage('在线任务未启动：'+(result.data.detail?.code||'模型或计划复核失败')+'。未执行任何步骤。','assistant');return;}addMessage('DeepSeek 已提出草案；服务端复核通过。只读查询已完成，后续改动逐步征求授权。','assistant');await loadSession();}
async function loadTaskPanel(){const panel=document.querySelector('#task-panel');if(!panel)return;const latest=await fetch('/api/v1/tasks/latest/current');if(!latest.ok)return;const taskId=(await latest.json()).task_id;if(!taskId){document.querySelector('#task-status').textContent='输入完整示范目标，在线模型先提出草案，服务端复核后启动任务。';document.querySelector('#task-steps').replaceChildren();document.querySelector('#task-controls').replaceChildren();return;}const response=await fetch('/api/v1/tasks/'+encodeURIComponent(taskId));if(response.ok)renderTaskPanel(await response.json());}
function taskButton(label,action){const button=document.createElement('button');button.type='button';button.textContent=label;button.addEventListener('click',async()=>{button.disabled=true;try{await action();}finally{button.disabled=false;}});return button;}
function renderTaskPanel(task){const labels={A:'A · 并行查询账单、订阅、账户',B:'B · 取消云盘订阅',C:'C · 等待并核对 11 月 5 日工资',D:'D · 偿还信用卡 ¥2,840',E:'E · 给妈妈转 ¥2,000',F:'F · 汇总核验'};const states={active:'进行中',completed:'已汇总',succeeded:'已完成',ready:'待处理',waiting:'等待前置条件',awaiting_authorization:'待独立授权',paused:'已暂停',cancelled:'已取消',failed:'未成功',blocked:'前置失败',frozen:'待核实冻结',needs_review:'待核实'};document.querySelector('#task-status').textContent='任务 '+task.task_id+' · '+(task.status==='needs_review'?'待核实，停止写入':states[task.status]||task.status)+' · 模拟环境';const list=document.querySelector('#task-steps');list.replaceChildren();const controls=document.querySelector('#task-controls');controls.replaceChildren();Object.entries(task.steps).forEach(([id,step])=>{const card=cardElement('status-card',labels[id]);const info=document.createElement('p');info.textContent='级别：'+step.risk_level+' · '+step.risk_reason+'；状态：'+(states[step.status]||step.status)+'；前置：'+(step.depends_on.join('、')||'无');card.append(info);if(step.details?.authorized_by){const auth=document.createElement('p');auth.className='muted';auth.textContent='授权：当前访客会话仅确认本步骤（'+step.details.authorization_scope+'）；操作 ID：'+(step.operation_id||'无');card.append(auth);}if(id==='A'){const evidence=document.createElement('p');evidence.className='muted';evidence.textContent='10 月账单 '+step.evidence.bill_transaction_count+' 笔 · 云盘 '+money(step.evidence.cloud_amount_cents)+'/月 · 借记卡 '+money(step.evidence.debit_balance_cents)+' · 卡账 '+money(step.evidence.card_balance_cents);card.append(evidence);}if(id==='B'&&step.status==='ready'&&task.status==='active'){card.append(taskButton('确认取消虚构云盘订阅',async()=>{if(!window.confirm('确认取消云盘 ¥30/月？不退款，也不计入当日到账资金。'))return;const r=await taskRequest('/api/v1/tasks/'+task.task_id+'/steps/B/execute',{confirmed:true});addMessage(r.ok&&r.data.receipt?.status==='succeeded'?'模拟云盘订阅已取消，回执已记录。':'取消未完成：'+(r.data.detail?.code||r.data.status),'assistant');await loadSession();}));}if(id==='C'&&step.status==='waiting'&&task.status==='active'){card.append(taskButton('核对工资并恢复',async()=>{const r=await taskRequest('/api/v1/tasks/'+task.task_id+'/steps/C/check',null);addMessage(r.ok?'工资事件及余额已核对。':'仍需由用户推进模拟日期并应用工资事件。','assistant');await loadSession();}));}if(['D','E'].includes(id)&&step.status==='ready'&&task.status==='active'){card.append(taskButton('准备本步骤并查看参数',async()=>{const r=await taskRequest('/api/v1/tasks/'+task.task_id+'/steps/'+id+'/prepare',null,{'Idempotency-Key':crypto.randomUUID()});addMessage(r.ok?'已准备 '+labels[id]+'；请对本步骤重新验证和确认。':'准备失败：'+(r.data.detail?.code||'状态变化'),'assistant');await loadSession();}));}if(['D','E'].includes(id)&&step.status==='awaiting_authorization'&&task.status==='active'){const operationId=step.operation_id;const code=document.createElement('input');code.placeholder='模拟验证码';code.inputMode='numeric';code.autocomplete='off';code.setAttribute('aria-label',labels[id]+'模拟验证码');const feedback=document.createElement('p');feedback.className='muted';feedback.textContent='授权对象：仅本步骤 '+labels[id]+'；模拟码不是多因子认证。';const verify=taskButton('验证本步骤',async()=>{const r=await taskRequest('/api/v1/operations/'+operationId+'/verify',{code:code.value});feedback.textContent=r.ok?'验证通过，仍需确认相同参数。':r.data.detail?.code==='SESSION_LOCKED'?'当前会话已锁定 15 分钟；到期后请重新验证并确认同一参数。':'验证未通过：'+(r.data.detail?.code||'错误');confirm.disabled=!r.ok;});const confirm=taskButton('确认参数并执行',async()=>{const confirmed=await taskRequest('/api/v1/operations/'+operationId+'/confirm',{summary_hash:step.details.summary_hash});if(!confirmed.ok){feedback.textContent='同参数确认失败：'+(confirmed.data.detail?.code||'错误');return;}const executed=await taskRequest('/api/v1/operations/'+operationId+'/execute',null);if(executed.data.receipt)showReceipt(executed.data.receipt);addMessage(executed.ok?'步骤已返回回执，任务将核对账本。':'执行结果待核实，停止后续写入。','assistant');await loadSession();});confirm.disabled=true;card.append(code,feedback,verify,confirm);if(step.details.risk_advice){const risk=document.createElement('p');risk.className='muted';risk.textContent='实验性模拟关注分（非欺诈概率） '+step.details.risk_advice.score+'；'+step.details.risk_advice.reasons.map(item=>item.label+' '+item.contribution).join('、')+'。独立虚构留出集未优于固定规则；评分不决定权限。';card.append(risk);}}if(['B','D','E'].includes(id)&&['ready','awaiting_authorization'].includes(step.status)&&['active','paused'].includes(task.status)){card.append(taskButton('执行前取消此步骤',async()=>{const r=await taskRequest('/api/v1/tasks/'+task.task_id+'/steps/'+id+'/cancel',null);addMessage(r.ok?'步骤已取消；成功资金操作没有撤销入口。':'无法取消：'+(r.data.detail?.code||'状态变化'),'assistant');await loadSession();}));}if(id==='F'&&step.status==='ready'&&task.status==='active'){card.append(taskButton('核验回执与余额并汇总',async()=>{const r=await taskRequest('/api/v1/tasks/'+task.task_id+'/steps/F/complete',null);addMessage(r.ok?(r.data.status==='verified_complete'?'已核验借记卡 '+money(r.data.debit_balance_cents)+'、信用卡账单归零；云盘取消不计作到账。':'任务部分完成或待核实，请查看逐步状态。'):'尚不能汇总：'+(r.data.detail?.code||'错误'),'assistant');await loadSession();}));}if(step.status==='needs_review'&&['D','E'].includes(id)){card.append(taskButton('按操作 ID 核对',async()=>{const r=await taskRequest('/api/v1/tasks/'+task.task_id+'/steps/'+id+'/reconcile',{confirmed_reissue:false});addMessage(r.ok?'核对结果：'+r.data.status:'结果仍待核实，未重试资金写入。','assistant');await loadSession();}));}list.append(card);});if(task.status==='active')controls.append(taskButton('暂停任务',async()=>{await taskRequest('/api/v1/tasks/'+task.task_id+'/pause',null);await loadSession();}));if(task.status==='paused')controls.append(taskButton('恢复任务并重新核验',async()=>{await taskRequest('/api/v1/tasks/'+task.task_id+'/resume',null);await loadSession();}));if(task.status==='needs_review')controls.append(taskButton('请求人工处理',async()=>{const r=await taskRequest('/api/v1/tasks/'+task.task_id+'/human',null);addMessage(r.ok?'已记录人工处理请求；未重试资金写入。':'人工请求未记录。','assistant');await loadSession();}));}
const renderTaskPanelCore=renderTaskPanel;
renderTaskPanel=function(task){renderTaskPanelCore(task);const ids=Object.keys(task.steps);const list=document.querySelector('#task-steps');['B','D','E'].forEach(id=>{const step=task.steps[id];if(!step||step.status!=='needs_review')return;const card=list.children[ids.indexOf(id)];if(!card)return;card.querySelectorAll('button').forEach(button=>{if(button.textContent==='按操作 ID 核对')button.remove();});const pending=document.createElement('p');pending.className='muted';pending.textContent='待核实操作 ID：'+(step.operation_id||'无')+'；仍未知时不得自动重试。';const feedback=document.createElement('p');feedback.className='muted';const check=taskButton('按操作 ID 核对',async()=>{const r=await taskRequest('/api/v1/tasks/'+task.task_id+'/steps/'+id+'/reconcile',{confirmed_reissue:false});if(!r.ok){feedback.textContent=r.data.detail?.code==='RESULT_STILL_UNKNOWN'?'核对结果仍未知，未重试任何写入。可请求人工处理。':'核对未完成：'+(r.data.detail?.code||'状态变化');return;}if(r.data.status==='failed_confirmed'){feedback.textContent='已证实失败。重新发起必须由你明确同意。';if(!card.querySelector('[data-action="reissue"]')){const reissue=taskButton('同意重新发起本步骤',async()=>{if(!window.confirm('已证实原操作失败。确认同意重新发起本步骤？'))return;const authorized=await taskRequest('/api/v1/tasks/'+task.task_id+'/steps/'+id+'/reconcile',{confirmed_reissue:true});addMessage(authorized.ok?'已记录重新发起授权；后续仍需重新准备并确认。':'重新发起授权未记录：'+(authorized.data.detail?.code||'错误'),'assistant');await loadSession();});reissue.dataset.action='reissue';card.append(reissue);}return;}addMessage(r.data.status==='succeeded'?'核对证实成功，已解除本步骤冻结；后续步骤仍需各自授权。':'核对结果：'+r.data.status,'assistant');await loadSession();});card.append(pending,feedback,check);});};
function clearConversation(){const messages=document.querySelector('#conversation-messages');if(messages)messages.replaceChildren();const receipt=document.querySelector('#receipt');if(receipt)receipt.replaceChildren();const flow=document.querySelector('#transfer-flow');if(flow)flow.replaceChildren();}
const transfer=document.querySelector('#transfer');if(transfer){const input=transfer.elements.text;transfer.addEventListener('submit',event=>{event.preventDefault();const text=input.value.trim();if(!text)return;transferIdempotencyKey=null;addMessage(text,'user');const button=transfer.querySelector('button[type=submit]');button.disabled=true;(isFixedGoal(text)?startFixedTask(text):parseIntent(text,'rules')).finally(()=>{button.disabled=false;input.value='';input.style.height='';});});input.addEventListener('input',()=>{transferIdempotencyKey=null;input.style.height='auto';input.style.height=Math.min(input.scrollHeight,112)+'px';});document.querySelectorAll('[data-suggestion]').forEach(button=>button.addEventListener('click',()=>{input.value=button.dataset.suggestion;input.focus();}));}
const replayButton=document.querySelector('#replay-example');if(replayButton)replayButton.addEventListener('click',()=>{const text='给妈妈转 2000 元生活费';addMessage(text,'user');parseIntent(text,'replay');});
const idForm=document.querySelector('#result-query');if(idForm)idForm.addEventListener('submit',event=>{event.preventDefault();queryReceipt('/api/v1/operations/'+encodeURIComponent(new FormData(idForm).get('operation_id')));});
const naturalForm=document.querySelector('#natural-query');if(naturalForm)naturalForm.addEventListener('submit',event=>{event.preventDefault();const query=new FormData(naturalForm).get('query');const id=extractOperationId(query);queryReceipt(id?'/api/v1/operations/'+encodeURIComponent(id):'/api/v1/operations/latest');});
const sessionQuery=document.querySelector('#session-status-query');if(sessionQuery)sessionQuery.addEventListener('submit',event=>{event.preventDefault();const query=new FormData(sessionQuery).get('query');const id=extractOperationId(query);queryReceipt(id?'/api/v1/operations/'+encodeURIComponent(id):'/api/v1/operations/latest','#session-status');});
const reset=document.querySelector('#reset-session');if(reset)reset.addEventListener('click',async()=>{if(!window.confirm('清除当前模拟会话中的变更、操作和详细审计，并恢复固定虚构数据？验证锁定仍会保留。'))return;const response=await fetch('/api/v1/session/reset',{method:'POST'});if(!response.ok){document.querySelector('#reset-status').textContent='重置失败，请稍后重试。';return;}clearConversation();if(sessionQuery)document.querySelector('#session-status').replaceChildren();if(transfer)transfer.reset();await loadSession();document.querySelector('#reset-status').textContent='当前模拟会话已恢复初始虚构数据。验证锁定仍保留。';});

loadSession();
</script></body></html>"""
