let model = { products: [], archived_products: [], drafts: [], publish_tasks: [], inquiries: [], audit_events: [] };
let selectedDraftId = null;
let aiConfig = {configured:false, model:null};
let aiKnowledge = {version:0,updated_at:null,policies:{},quick_replies:[]};
let aiDraftAdvice = null;
let browserState = {state:'disconnected'};
let liveConversations = [];
let selectedInboxAccountKey = null;
let selectedConversationId = null;
let liveMessages = [];
let loadingConversationId = null;
let refreshingConversations = false;
let selectedProductIds = new Set();
let selectedChromeAccount = false;
let accountCardSignature = null;
let batchPolling = false;
let activeBatchTaskIds = new Set();
let expandedTaskIds = new Set();
let accountStatusPolling = false;
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const facebookUrl = (value) => {try{const url=new URL(value);return url.protocol==='https:'&&url.hostname==='www.facebook.com'?url.href:null;}catch{return null;}};
const avatarUrl = (value) => {try{const url=new URL(value);return url.protocol==='https:'&&(/(^|\.)fbcdn\.net$/.test(url.hostname)||url.hostname==='www.facebook.com')?url.href:null;}catch{return null;}};
const money = (p) => `${(p.price_minor / 100).toFixed(2)} ${esc(p.currency)}`;
const tag = (value) => `<span class="pill ${value === 'approved' || value === 'posted' || value === 'sent_manually' ? '' : value === 'failed' || value === 'blocked' || value === 'uncertain' ? 'orange' : value === 'pending' || value === 'queued' || value === 'preparing' ? 'gold' : 'gray'}">${esc({pending:'待审核',approved:'已批准',queued:'待发布',preparing:'浏览器执行中',blocked:'平台阻止',uncertain:'结果待核实',posted:'已发布',failed:'发布失败',needs_attention:'待处理',draft:'回复草稿',sent_manually:'手动发送',new:'新询盘',responded:'已回复',negotiating:'商谈中',won:'成交',lost:'未成交'}[value] || value)}</span>`;
let toastTimer;
function toast(message, error=false) {
  const box = $('toast'); box.textContent = message; box.className = `toast show${error ? ' error' : ''}`;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => box.className='toast', 4200);
}
async function api(path, method='GET', body) {
  const response = await fetch(path, {method, headers: body ? {'Content-Type':'application/json'} : {}, body: body ? JSON.stringify(body) : undefined});
  let data;
  try { data = await response.json(); } catch { throw new Error(`请求失败 (${response.status})`); }
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === 'string' ? detail : (detail?.issues || []).join('；') || JSON.stringify(detail));
  }
  return data;
}
async function withLoading(element, work) {
  if (!element || element.classList.contains('is-loading')) return;
  const priorAriaDisabled = element.getAttribute('aria-disabled');
  const loadingLabel = element.dataset.loadingLabel || (element.id==='refresh-account' ? '刷新中…' : null);
  const originalHtml = loadingLabel ? element.innerHTML : null;
  if (loadingLabel) element.textContent = loadingLabel;
  element.classList.add('is-loading');
  element.setAttribute('aria-busy','true');
  element.setAttribute('aria-disabled','true');
  const shownAt = performance.now();
  await new Promise(resolve => {
    let finished = false;
    const finish = () => { if (!finished) { finished = true; clearTimeout(fallback); resolve(); } };
    const fallback = setTimeout(finish, 150);
    requestAnimationFrame(() => requestAnimationFrame(finish));
  });
  try { return await work(); }
  finally {
    const remaining = 500 - (performance.now() - shownAt);
    if (remaining > 0) await new Promise(resolve => setTimeout(resolve, remaining));
    element.classList.remove('is-loading');
    element.removeAttribute('aria-busy');
    if (priorAriaDisabled === null) element.removeAttribute('aria-disabled');
    else element.setAttribute('aria-disabled',priorAriaDisabled);
    if (originalHtml !== null) element.innerHTML = originalHtml;
  }
}
function onBusyClick(id, work) {
  $(id).addEventListener('click', async e => {
    try { await withLoading(e.currentTarget, work); }
    catch(err) { toast(err.message,true); }
  });
}
function renderPage() {
  const page = location.pathname === '/inbox' ? 'inbox' : location.pathname === '/ai-settings' ? 'ai' : 'listing';
  document.body.classList.toggle('inbox-page', page==='inbox');
  document.querySelectorAll('[data-page]').forEach(element => element.classList.toggle('hidden', element.dataset.page !== page));
  document.querySelectorAll('[data-route]').forEach(link => {
    const active = link.dataset.route === page;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  document.title = `${{inbox:'Marketplace 会话',ai:'AI 模型设置',listing:'上架工作台'}[page]} · Marketplace 运营工作台`;
}
document.addEventListener('click', e => {
  const link = e.target.closest('a[data-route]');
  if (!link || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  if (location.pathname !== link.pathname) history.pushState({}, '', link.pathname);
  renderPage();
  if(link.dataset.route==='inbox')enterInbox();
  window.scrollTo(0, 0);
});
document.addEventListener('click', async e => {
  const link = e.target.closest('a[data-download]');
  if (!link || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  try { await withLoading(link, async () => {
    const response=await fetch(link.href);
    if(!response.ok)throw new Error(`下载失败 (${response.status})`);
    const objectUrl=URL.createObjectURL(await response.blob());
    const anchor=document.createElement('a');
    anchor.href=objectUrl;
    anchor.download=link.dataset.download==='template'?'商品导入模板.xlsx':'marketplace-workbench-export.json';
    document.body.append(anchor);anchor.click();anchor.remove();
    setTimeout(()=>URL.revokeObjectURL(objectUrl),30000);
  }); } catch(err) { toast(err.message,true); }
});
window.addEventListener('popstate', () => {renderPage();if(location.pathname==='/inbox')enterInbox();});
renderPage();
function enterInbox() {
  const checking=$('inbox-account-check'), layout=document.querySelector('.inbox-layout');
  checking.classList.remove('hidden');layout.classList.add('hidden');
  api('/api/browser/status').then(status=>setBrowserState(status)).catch(err=>
    setBrowserState({state:'error',error:`账号状态读取失败：${err.message}`},true)
  ).finally(()=>{checking.classList.add('hidden');layout.classList.remove('hidden');});
}
async function refresh() { model = await api('/api/state'); render(); }
function renderAISettings() {
  const configured=!!aiConfig.configured;
  const indicator=$('ai-settings-indicator');
  indicator.textContent=configured?'已配置':'未配置';
  indicator.classList.toggle('ready',configured);
  $('ai-settings-detail').textContent=configured?`当前使用 ${aiConfig.model} · ${aiConfig.source==='local'?'本机保存':'环境变量'}`:'默认模型和回复提示词已预填；填写 API Key 后保存即可使用。';
  $('ai-provider').value=aiConfig.provider||'siliconflow';
  $('ai-model').value=aiConfig.model||'';
  $('ai-base-url').value=aiConfig.base_url||'https://api.siliconflow.cn/v1';
  $('ai-api-key').value='';
  $('ai-key-hint').textContent=aiConfig.source==='environment'&&aiConfig.api_key_set?'当前密钥来自启动环境变量；改用网页保存设置时，请重新输入 API Key。':aiConfig.api_key_set?'当前密钥已保存，留空可保留；更换服务类型或 URL 时请重新输入。':'当前没有可用密钥；请输入 API Key 后保存。';
  $('ai-system-prompt').value=aiConfig.reply_system_prompt||'';
}
const knowledgeIntents={general:'通用',availability:'库存/是否有货',price:'价格',size:'尺寸/规格',shipping:'配送',pickup:'面交',payment:'付款',returns:'退换',negotiation:'议价'};
function quickReplyHtml(item={intent:'general',title:'',content:''}) {
  return `<div class="ai-quick-row"><div class="field"><label>问题类型</label><select class="ai-quick-intent">${Object.entries(knowledgeIntents).map(([key,label])=>`<option value="${key}" ${item.intent===key?'selected':''}>${label}</option>`).join('')}</select></div><div class="field ai-quick-title"><label>话术标题</label><input class="ai-quick-title-input" maxlength="80" value="${esc(item.title)}" placeholder="例如：询问邮寄"></div><button class="button small warn ai-quick-remove" type="button">删除</button><div class="field ai-quick-content"><label>参考措辞</label><textarea class="ai-quick-content-input" maxlength="1200" placeholder="只写已审核的回复措辞，避免未经证实的承诺。">${esc(item.content)}</textarea></div></div>`;
}
function renderAIKnowledge() {
  for(const key of ['shipping','pickup','payment','returns','negotiation']) $(`ai-policy-${key}`).value=aiKnowledge.policies?.[key]||'';
  $('ai-knowledge-version').textContent=aiKnowledge.version?`第 ${aiKnowledge.version} 版 · 最近更新 ${aiKnowledge.updated_at?.replace('T',' ').slice(0,19)||''} UTC`:'正在使用默认经营规则和话术 · 可修改后保存';
  $('ai-quick-replies').innerHTML=aiKnowledge.quick_replies?.length?aiKnowledge.quick_replies.map(quickReplyHtml).join(''):'<div class="empty">尚未添加参考话术。</div>';
}
function adviceHtml(advice) {
  if(!advice)return '';
  const basis=advice.basis||{};
  const summary=advice.requires_review?`需人工确认：${(advice.review_notes||[]).join('；')}`:'AI 草稿已生成，请核对后发送';
  return `<details class="ai-advice ${advice.requires_review?'needs-review':''}"><summary>${esc(summary)}</summary><p>Agent 查询：${esc((basis.tool_trace||[]).join(' → ')||'无')}</p><p>会话 ${basis.history_count||0} 条 · 客服知识第 ${basis.knowledge_version||0} 版</p><p>经营规则：${esc((basis.policies||[]).join('、')||'无')}；参考话术：${esc((basis.reference_scripts||[]).join('、')||'无')}</p></details>`;
}
function productForDraft(d) { return [...model.products, ...model.archived_products].find(p => p.id === d.product_id); }
function draftForTask(t) { return model.drafts.find(d => d.id === t.draft_id); }
function productForTask(t) { const d = draftForTask(t); return d ? productForDraft(d) : null; }
function render() {
  $('metric-products').textContent = model.products.length;
  $('metric-drafts').textContent = model.drafts.filter(d => d.status === 'pending' && model.products.some(p => p.id === d.product_id)).length;
  $('metric-tasks').textContent = model.publish_tasks.length;
  renderProducts(); renderDraft(); renderTasks(); renderInquiries(); renderAudit(); renderInbox();
}
function renderProducts() {
  $('product-rows').innerHTML = model.products.length ? model.products.map(p => `
    <tr><td><input type="checkbox" class="product-check" data-product-id="${p.id}" ${selectedProductIds.has(p.id)?'checked':''} ${p.currency==='USD'?'':'disabled title="当前仅支持 USD 商品"'}></td><td><strong>${esc(p.name)}</strong><small>${esc(p.sku)} · ${esc(p.category)}</small></td>
    <td><span class="muted">${esc(p.material)} · ${esc(p.size)} · ${esc(p.color)}</span></td>
    <td><strong>${money(p)}</strong></td><td>${p.stock}</td>
    <td><small>${p.image_path?'✓ 本地图片已关联':'未关联本地图片'}</small><label class="button small secondary file-button">选择图片 <input type="file" class="single-product-image" data-product-id="${p.id}" accept="image/jpeg,image/png,image/webp" hidden></label> <button class="button small secondary" data-action="new-draft" data-id="${p.id}">查看草稿</button></td></tr>`).join('') : '<tr><td colspan="6"><div class="empty">尚未导入商品。下载样例表填写后，再选择 CSV / Excel 商品表导入。</div></td></tr>';
}

function renderBrowserStatus() {
  const states={disconnected:'未连接',connecting:'正在建立连接',awaiting_permission:'等待 Chrome 授权',online:'在线',no_facebook_tab:'未找到 Facebook 页面',login_required:'需要登录 Facebook',error:'连接失败'};
  const online=browserState.state==='online', name=browserState.display_name;
  const timedOut=browserState.state==='awaiting_permission'&&/timed out|timeout/i.test(browserState.error||'');
  const showConnect=['disconnected','error'].includes(browserState.state);
  $('hero-connection').className=`hero-connection ${online?'is-online':!timedOut&&(browserState.state==='connecting'||browserState.state==='awaiting_permission')?'is-pending':'is-offline'}`;
  $('hero-connection-label').textContent=online?'Chrome 已连接':browserState.state==='no_facebook_tab'?'Chrome 已连接 · 未找到 Facebook 页面':timedOut?'Chrome 连接超时':browserState.state==='awaiting_permission'?'等待 Chrome 授权':browserState.state==='connecting'?'正在连接 Chrome':`Chrome ${states[browserState.state]||'未连接'}`;
  $('hero-connection-hint').textContent=online?'已识别 1 个已登录账号，请在下方勾选':browserState.state==='no_facebook_tab'?'请在同一 Chrome 资料目录打开 Facebook，登录后点“刷新状态”':'当前仅支持 Chrome 默认资料目录的单账号验证';
  $('connect-browser').classList.toggle('hidden',!showConnect);
  $('browser-state').textContent=timedOut?'Chrome 连接超时':`Chrome ${states[browserState.state]||browserState.state}`;
  $('browser-state').className=online?'browser-status-good':'browser-status-warn';
  $('account-connection-detail').classList.toggle('hidden',online&&!browserState.error);
  $('browser-detail').textContent=browserState.state==='no_facebook_tab'?'当前默认 Chrome 资料目录没有 Facebook 标签页。打开并登录 Facebook 后，点击上方“刷新状态”。':timedOut?'Chrome 调试连接未完成。请确认远程调试已开启并允许连接，再点“刷新状态”重试。':browserState.error||(online?(name?'':'Facebook 已登录，但个人资料名称尚未识别。请刷新状态，并在 Chrome 核对当前账号。'):'请在 Chrome 登录 Facebook，连接后可在这里查看账号。');
  $('browser-detail').classList.toggle('hidden',online&&!!name&&!browserState.error);
  $('refresh-account').disabled=['disconnected','connecting','error'].includes(browserState.state);
  $('disconnect-browser').classList.toggle('hidden',!browserState.account_key&&!['no_facebook_tab','login_required'].includes(browserState.state));
  $('refresh-conversations').disabled=!online||!name||browserState.account_key!==selectedInboxAccountKey||loadingConversationId!==null||refreshingConversations;
  $('inbox-account-state').classList.toggle('hidden',online&&!!name);
  $('inbox-account-state').innerHTML=online?'Chrome 已连接，但还未识别账号名称。<a href="/" data-route="listing">前往上架工作台刷新账号 ↗</a>':'暂无在线 Facebook 账号。<a href="/" data-route="listing">前往上架工作台连接账号 ↗</a>';
  if(!online) selectedChromeAccount=false;
  $('select-all-accounts').disabled=!online;
  $('select-all-accounts').textContent=selectedChromeAccount?'取消全选':'全选账号';
  if(!online){accountCardSignature=null;$('account-select').innerHTML=`<div class="account-empty">${browserState.state==='no_facebook_tab'?'当前 Chrome 中没有 Facebook 页面。<a href="https://www.facebook.com/" target="_blank" rel="noopener noreferrer">打开 Facebook ↗</a>，登录后点击上方“刷新状态”。':browserState.state==='awaiting_permission'?'等待 Chrome 确认远程调试连接…':'暂无在线账号。连接并登录 Facebook 后，账号会显示在这里。'}</div>`;return;}
  const safeAvatar=avatarUrl(browserState.avatar_url),safeProfile=facebookUrl(browserState.profile_url);
  const signature=JSON.stringify([name,safeAvatar,safeProfile]);
  if(signature===accountCardSignature)return;
  accountCardSignature=signature;
  const initials=(name||'F').trim().split(/\s+/).slice(0,2).map(part=>part[0]||'').join('').toUpperCase();
  $('account-select').innerHTML=`<div class="account-card"><div class="account-identity"><div class="account-avatar"><span class="account-avatar-fallback">${esc(initials)}</span>${safeAvatar?`<img class="account-avatar-img" src="${esc(safeAvatar)}" alt="${esc(name||'Facebook')} 的头像" referrerpolicy="no-referrer">`:''}</div><div class="account-copy"><div class="account-name-line"><strong>${esc(name||'Facebook 账号（名称待识别）')}</strong><span class="account-online-dot">在线</span></div><div class="account-meta">Facebook Marketplace <span>·</span> Chrome 默认资料目录</div>${safeProfile?`<a class="account-profile-link" href="${esc(safeProfile)}" target="_blank" rel="noopener noreferrer">查看 Facebook 资料页 ↗</a>`:`<span class="account-verify-note">请在 Chrome 核对当前登录身份</span>`}</div></div><label class="account-picker" title="选择此账号"><input id="account-chrome-default" type="checkbox" aria-label="选择 ${esc(name||'当前 Facebook')} 账号" ${selectedChromeAccount?'checked':''}></label></div>`;
}
function renderInbox() {
  const accountList=$('live-account-list');
  const accountAvailable=browserState.state==='online'&&browserState.account_key&&browserState.display_name;
  const accountKey=accountAvailable?browserState.account_key:null;
  if(selectedInboxAccountKey!==accountKey) selectedInboxAccountKey=null;
  const name=browserState.display_name||'';
  const avatar=avatarUrl(browserState.avatar_url);
  accountList.innerHTML=accountAvailable?`<button class="inbox-account${selectedInboxAccountKey===accountKey?' active':''}${refreshingConversations?' is-loading':''}" type="button" data-action="select-inbox-account" data-account-key="${esc(accountKey)}" aria-pressed="${selectedInboxAccountKey===accountKey}" ${loadingConversationId!==null||refreshingConversations?'disabled':''}><span class="inbox-account-avatar">${avatar?`<img src="${esc(avatar)}" alt="" referrerpolicy="no-referrer">`:esc(name.trim().charAt(0).toUpperCase())}</span><span class="inbox-account-copy"><strong>${esc(name)}</strong><small>${refreshingConversations?'正在读取会话…':'点击读取当前会话'}</small></span></button>`:'<div class="empty">暂无已识别的在线 Facebook 账号。</div>';
  const conversations=selectedInboxAccountKey?liveConversations.filter(c=>c.account_key===selectedInboxAccountKey):[];
  $('metric-inquiries').textContent=conversations.filter(c=>c.needs_attention).length;
  $('inbox-summary').classList.toggle('hidden',!selectedInboxAccountKey);
  const list=$('live-conversation-list');
  const accountOnline=!!accountAvailable&&accountKey===selectedInboxAccountKey;
  $('refresh-conversations').disabled=!accountOnline||loadingConversationId!==null||refreshingConversations;
  list.innerHTML=(refreshingConversations?'<div class="inbox-syncing" role="status"><span class="loading-spinner" aria-hidden="true"></span>正在读取当前会话…</div>':'') + (conversations.length ? conversations.map(c=>`<button class="inbox-item ${c.id===selectedConversationId?'active':''}${c.id===loadingConversationId?' is-loading':''}" data-action="live-open" data-id="${c.id}" ${!accountOnline||loadingConversationId!==null||refreshingConversations?'disabled':''} aria-busy="${c.id===loadingConversationId}"><strong>${c.needs_attention?'<span class="attention-mark" aria-label="待处理">!</span>':''}${esc(c.title)}${c.id===loadingConversationId?'<span class="inbox-loading-label">加载中…</span>':''}</strong><small>${esc(c.last_preview || '尚无消息摘要')}</small><small>最近同步 ${esc(c.last_seen_at.replace('T',' ').slice(0,19))} UTC</small></button>`).join('') : `<div class="empty">${refreshingConversations?'请稍候…':selectedInboxAccountKey?'当前没有读取到 Marketplace 会话。':'先选择在线账号。'}</div>`);
  const thread=$('live-thread'); const conversation=conversations.find(c=>c.id===selectedConversationId);
  if(!conversation){thread.innerHTML='<div class="empty">选择一条会话查看历史。</div>';return;}
  if(loadingConversationId!==null){thread.innerHTML=`<div class="thread-loading" role="status"><span class="loading-spinner" aria-hidden="true"></span>正在读取 ${esc(conversation.title)} 的会话历史…</div>`;return;}
  const productOptions=model.products.filter(p=>p.currency==='USD').map(p=>`<option value="${p.id}">${esc(p.sku)} · ${esc(p.name)}</option>`).join('');
  thread.innerHTML=`<div class="thread-header"><div class="detail-head"><h3>${esc(conversation.title)}</h3>${conversation.needs_attention?'<span class="attention-mark">!</span>':''}</div><p class="caption">${esc(conversation.last_preview || '')}</p>
    <div class="thread-actions"><button class="button small secondary" data-action="live-read" data-id="${conversation.id}" data-loading-label="读取中…">刷新历史</button><button class="button small secondary" data-action="live-handled" data-id="${conversation.id}" data-loading-label="保存中…">标记已处理</button></div></div>
    <div class="message-list">${liveMessages.length?liveMessages.map(m=>`<div class="message ${m.direction==='out'?'out':''}"><small>${esc(m.sender)} · ${esc(m.time_label)}</small>${esc(m.text)}</div>`).join(''):'<div class="empty">点击“刷新历史”读取页面可见消息。</div>'}</div>
    <div class="thread-composer"><div class="field"><label for="live-reply">回复内容（发送前会再次确认账号、会话和文字）</label><textarea id="live-reply" maxlength="2000" placeholder="输入给买家的英文回复"></textarea></div>
    <div class="thread-actions"><button class="button small primary" data-action="live-send" data-id="${conversation.id}" data-loading-label="发送中…" ${browserState.state==='online'?'':'disabled'}>确认后发送</button></div>
    <div class="field" style="margin-top:15px"><label for="live-product">对应商品（自动识别失败时手选）</label><select id="live-product"><option value="">Agent 自动识别</option>${productOptions}</select></div>
    <div class="thread-actions"><button class="button small secondary" data-action="live-ai" data-id="${conversation.id}" data-loading-label="查询并生成中…" ${aiConfig.configured?'':'disabled title="请先配置模型服务"'}>客服 Agent 拟写回复</button></div>
    <p class="caption">AI 只生成草稿，不会自动发送；请核对商品事实、库存和配送承诺。</p><div id="live-ai-advice">${aiDraftAdvice?.conversationId===conversation.id?adviceHtml(aiDraftAdvice.result):''}</div></div>`;
}
function setBrowserState(status, forceRender=false) {
  const identityChanged=status.state!==browserState.state||status.account_key!==browserState.account_key||status.display_name!==browserState.display_name;
  const viewChanged=identityChanged||status.avatar_url!==browserState.avatar_url;
  if(status.state!=='online'||!status.display_name||browserState.state!=='online'||status.account_key!==browserState.account_key||status.display_name!==browserState.display_name){
    liveConversations=[];selectedInboxAccountKey=null;selectedConversationId=null;liveMessages=[];aiDraftAdvice=null;
  }
  browserState=status;renderBrowserStatus();
  if(forceRender||viewChanged)renderInbox();
  if(identityChanged)renderTasks();
}
async function loadBrowser() {
  setBrowserState(await api('/api/browser/status'),true);
}
async function refreshVisibleConversations() {
  const accountKey=selectedInboxAccountKey;
  if(browserState.state!=='online'||!browserState.display_name||accountKey!==browserState.account_key)
    throw new Error('请先选择已识别的在线 Facebook 账号');
  refreshingConversations=true;
  liveConversations=[];selectedConversationId=null;liveMessages=[];
  renderInbox();
  try {
    const result=await api('/api/browser/conversations/refresh','POST');
    if(browserState.account_key!==accountKey)throw new Error('账号已切换，请重新选择后读取会话');
    liveConversations=result.items.filter(c=>c.account_key===accountKey);
    toast(`当前读取到 ${result.visible_count} 条 Marketplace 会话`);
  } finally { refreshingConversations=false;renderInbox(); }
}
function renderDraft() {
  const panel = $('draft-panel');
  const currentDrafts = model.drafts.filter(d => model.products.some(p => p.id === d.product_id));
  const draft = currentDrafts.find(d => d.id === selectedDraftId) || currentDrafts[0];
  if (!draft) { panel.classList.add('hidden'); return; }
  selectedDraftId = draft.id;
  const product = productForDraft(draft);
  if (!product) return;
  panel.classList.remove('hidden');
  const options = currentDrafts.map(d => `<option value="${d.id}" ${d.id === draft.id ? 'selected' : ''}>${esc(productForDraft(d)?.sku)} · 第 ${d.version} 版 · ${d.status === 'approved' ? '已批准' : '待审核'}</option>`).join('');
  let notes=[]; try { notes=JSON.parse(draft.review_notes || '[]'); } catch {}
  panel.innerHTML = `<div class="detail-head"><h3>草稿审核 <small style="font-size:11px;color:#9aa6a0">第 ${draft.version} 版</small></h3>${tag(draft.status)}</div>
    <div class="field"><label for="draft-picker">选择草稿</label><select id="draft-picker">${options}</select></div>
    <div class="caption">生成来源：${esc(draft.generation_source === 'llm' ? `LLM ${draft.model_id || ''}` : draft.generation_source === 'agent_skill' ? 'Agent Skill / MCP' : '固定模板')}</div>
    <div class="fact-strip">固定商品事实：${esc(product.sku)} · ${money(product)} · ${esc(product.material)} · ${esc(product.size)} · ${esc(product.color)} · 库存 ${product.stock}</div>
    <div class="field"><label for="draft-title">英文标题</label><input id="draft-title" maxlength="200" value="${esc(draft.title)}" ${draft.status === 'approved' ? 'disabled' : ''}></div>
    <div class="field" style="margin-top:14px"><label for="draft-description">英文描述</label><textarea id="draft-description" ${draft.status === 'approved' ? 'disabled' : ''}>${esc(draft.description)}</textarea></div>
    <div class="caption">规格来源：${esc(product.source_note)}。价格由商品字段控制；系统会拦截部分无依据卖点与不一致币种。</div>
    ${notes.length ? `<div class="report">AI 待确认：${esc(notes.join('；'))}</div>` : ''}
    <div class="detail-actions">${draft.status === 'pending' ? `<button class="button secondary" data-action="save-draft" data-id="${draft.id}">保存修改</button><button class="button primary" data-action="approve-draft" data-id="${draft.id}">人工批准草稿</button>` : ''}</div>`;
}
function renderTasks() {
  const list = $('task-list');
  if (!model.publish_tasks.length) { list.innerHTML = '<div class="empty">还没有发布任务。先导入商品，再建立上架任务。</div>'; return; }
  list.innerHTML = `<div class="table-wrap publish-task-table-wrap"><table class="publish-task-table"><thead><tr><th>商品 / SKU</th><th>发布账号</th><th>价格</th><th>状态</th><th>操作</th></tr></thead><tbody>${model.publish_tasks.map(t => {
    const p = productForTask(t), d = draftForTask(t);
    const expanded = expandedTaskIds.has(t.id);
    return `<tr class="publish-task-row${expanded?' is-expanded':''}">
      <td><strong>${esc(p?.name || '商品')}</strong><small>${esc(p?.sku || '')}</small></td>
      <td>${esc(t.account_alias)}</td>
      <td>${p ? money(p) : '—'}</td>
      <td>${tag(t.status)}</td>
      <td><button id="task-toggle-${t.id}" class="publish-task-toggle" type="button" data-action="toggle-task" data-id="${t.id}" aria-expanded="${expanded}" aria-controls="task-details-${t.id}" aria-label="${expanded?'收起':'展开'} ${esc(p?.name || p?.sku || '发布任务')} 的发布详情">${expanded?'收起':'展开'}</button></td>
      </tr><tr id="task-details-${t.id}" class="publish-task-detail-row${expanded?'':' hidden'}"><td colspan="5"><div class="publish-task-details">
      <div class="section-kicker">任务 #${t.id} · ${esc(p?.sku || '')}</div>
      <p>${esc(d?.title || '')}</p>
      <p class="caption">销售列表显示对应商品（含审核中）或核验到商品链接后，工作台标记为已发布。</p>
      ${t.marketplace_url ? `<p><a href="${esc(t.marketplace_url)}" target="_blank" rel="noopener noreferrer">查看${t.marketplace_url.includes('/share/') ? ' Facebook 分享' : ' Marketplace'}链接 ↗</a></p>` : ''}
      ${t.note ? (['failed','blocked','needs_attention','uncertain'].includes(t.status)
        ? `<div class="publish-task-error"><strong>${t.status==='uncertain'?'发布结果待核实':'发布未完成 · 失败原因'}</strong><span>${esc(t.note)}</span></div>`
        : `<p>备注：${esc(t.note)}</p>`) : ''}
      ${t.status==='uncertain' ? `<button class="button small secondary" data-action="task-verify" data-id="${t.id}" ${browserState.state==='online'?'':'disabled title="请先连接 Chrome"'}>核验 Facebook 销售列表</button>` : ''}
      ${!['posted','preparing'].includes(t.status) ? `<div class="mini-form"><input id="task-url-${t.id}" placeholder="Marketplace 商品链接或 Facebook 分享链接"><input id="task-note-${t.id}" placeholder="结果备注（可选）"></div>
      <div class="card-footer"><button class="button small primary" data-action="task-posted" data-id="${t.id}">确认已手动发布</button><button class="button small warn" data-action="task-failed" data-id="${t.id}">记录失败</button><button class="button small secondary" data-action="task-attention" data-id="${t.id}">待处理</button></div>` : `<div class="card-footer"><span class="caption">发布结果已回填 · ${esc(t.updated_at.slice(0,10))}</span></div>`}
      </div></td></tr>`;
  }).join('')}</tbody></table></div>`;
}
function renderInquiries() {
  const select = $('inquiry-task');
  const prior = select.value;
  select.innerHTML = '<option value="">选择关联发布任务</option>' + model.publish_tasks.map(t => `<option value="${t.id}">#${t.id} · ${esc(productForTask(t)?.sku)} · ${esc(t.account_alias)}</option>`).join('');
  select.value = prior;
  const list = $('inquiry-list');
  if (!model.inquiries.length) { list.innerHTML = '<div class="empty">暂无手工询盘记录。</div>'; return; }
  list.innerHTML = model.inquiries.map(i => {
    const task = model.publish_tasks.find(t => t.id === i.task_id);
    let replyNotes=[]; try { replyNotes=JSON.parse(i.ai_notes || '[]'); } catch {}
    return `<article class="card"><div class="card-top"><span class="section-kicker">INQUIRY #${i.id} · ${esc(i.buyer_alias)}</span>${tag(i.stage)}</div>
      <h3>${esc(productForTask(task)?.name || '')}</h3><p><b>买家问题</b> · ${esc(i.question)}</p>
      <div class="field" style="margin-top:13px"><label for="reply-${i.id}">英文回复草稿 ${tag(i.reply_status)}</label><textarea id="reply-${i.id}" ${i.reply_status === 'sent_manually' ? 'disabled' : ''}>${esc(i.reply_draft)}</textarea></div>
      <div class="caption">生成来源：${esc(i.reply_source === 'llm' ? `LLM ${i.reply_model || ''}` : i.reply_source === 'agent_skill' ? 'Agent Skill / MCP' : '固定模板')}</div>
      ${replyNotes.length ? `<div class="report">AI 待确认：${esc(replyNotes.join('；'))}</div>` : ''}
      <div class="card-footer">${i.reply_status !== 'sent_manually' ? `<button class="button small secondary" data-action="save-reply" data-id="${i.id}">保存草稿</button><button class="button small secondary" data-action="ai-reply" data-id="${i.id}" ${aiConfig.configured ? '' : 'disabled title="请先配置模型"'}>AI 重写</button><button class="button small primary" data-action="approve-reply" data-id="${i.id}">人工批准回复</button>` : ''}${i.reply_status === 'approved' ? `<button class="button small ghost" data-action="sent-reply" data-id="${i.id}">记录已手动发送</button>` : ''}</div>
      <div class="mini-form three"><select id="stage-${i.id}">${['new','responded','negotiating','won','lost'].map(v => `<option value="${v}" ${i.stage === v ? 'selected' : ''}>${{new:'新询盘',responded:'已回复',negotiating:'商谈中',won:'成交',lost:'未成交'}[v]}</option>`).join('')}</select>
      <select id="channel-${i.id}">${['marketplace','whatsapp_manual','other_manual'].map(v => `<option value="${v}" ${i.channel === v ? 'selected' : ''}>${{marketplace:'Marketplace 手动',whatsapp_manual:'WhatsApp 手动',other_manual:'其他手动'}[v]}</option>`).join('')}</select>
      <input id="followup-${i.id}" type="datetime-local" value="${esc(i.next_followup_at || '')}"></div>
      <div class="card-footer"><button class="button small secondary" data-action="save-followup" data-id="${i.id}">保存跟进</button></div></article>`;
  }).join('');
}
function renderAudit() {
  const names = {product_imported:'导入商品',draft_created:'建立草稿',ai_draft_created:'LLM 草稿',agent_draft_created:'Skill 草稿',draft_edited:'修改草稿',draft_approved:'人工批准草稿',task_queued:'加入发布队列',publish_result_recorded:'回填发布结果',inquiry_recorded:'录入询盘',ai_reply_created:'LLM 回复草稿',agent_reply_created:'Skill 回复草稿',reply_status_changed:'回复状态更新',followup_updated:'更新跟进'};
  $('audit-list').innerHTML = model.audit_events.length ? model.audit_events.slice(0,15).map(e => `<div class="audit-row"><div><b>${esc(names[e.event_type] || e.event_type)}</b> · ${esc(e.entity_type)} #${e.entity_id} <span>${esc(e.detail)}</span></div><span>${esc(e.created_at.replace('T',' ').slice(0,19))} UTC</span></div>`).join('') : '<div class="empty">操作记录会显示在这里。</div>';
}
async function perform(action, id) {
  if (action === 'toggle-task') { expandedTaskIds.has(id)?expandedTaskIds.delete(id):expandedTaskIds.add(id); renderTasks(); $(`task-toggle-${id}`)?.focus(); return; }
  if (action === 'live-open' || action === 'live-read') {
    if (loadingConversationId!==null) return;
    const previousId=selectedConversationId, previousMessages=liveMessages;
    selectedConversationId=id;liveMessages=[];aiDraftAdvice=null;loadingConversationId=id;renderInbox();
    try { const r=await api(`/api/browser/conversations/${id}/read`,'POST');liveMessages=r.messages;await loadBrowser(); }
    catch(err){selectedConversationId=previousId;liveMessages=previousMessages;throw err;}
    finally{loadingConversationId=null;renderInbox();}
    return;
  }
  if (action === 'live-handled') { await api(`/api/browser/conversations/${id}/handled`,'POST'); const item=liveConversations.find(c=>c.id===id);if(item)item.needs_attention=0;renderInbox();return; }
  if (action === 'live-send') { const c=liveConversations.find(x=>x.id===id), body=$('live-reply').value.trim(); if(!body) throw new Error('请先填写回复内容');if(!c)throw new Error('会话已不在当前列表，请重新读取');if(!window.confirm(`发送到 ${browserState.display_name} 的会话“${c.title}”？\n\n${body}`)) return; const r=await api(`/api/browser/conversations/${id}/send`,'POST',{message:body}); toast(r.message,r.state!=='sent'); if(r.state==='sent'){c.needs_attention=0;aiDraftAdvice=null;liveMessages=(await api(`/api/browser/conversations/${id}/read`,'POST')).messages;await loadBrowser();}return; }
  if (action === 'live-ai') { const productId=Number($('live-product').value)||null;if(!liveMessages.length)throw new Error('请先读取会话历史');if(liveMessages[liveMessages.length-1].direction!=='in')throw new Error('最新一条消息不是买家发来的消息，请先核对会话');if(!window.confirm(`客服 Agent 会将当前问题和最近会话发给配置的模型服务，按需查询本地商品、完整会话历史和经营规则，再生成草稿。请确认消息中没有敏感信息。`))return; const r=await api(`/api/browser/conversations/${id}/reply-ai`,'POST',{product_id:productId,allow_external_processing:true}); $('live-reply').value=r.answer;aiDraftAdvice={conversationId:id,result:r};$('live-ai-advice').innerHTML=adviceHtml(r);toast(r.review_notes.length?`AI 草稿已填入；待确认：${r.review_notes.join('；')}`:'AI 草稿已填入，请人工核对后发送');return; }
  if (action === 'new-draft') { const r = await api(`/api/products/${id}/draft`, 'POST'); selectedDraftId = r.id; toast('草稿已建立'); }
  if (action === 'ai-draft') { const r = await api(`/api/products/${id}/draft-ai`, 'POST'); selectedDraftId = r.id; toast(r.issues.length ? `AI 草稿已保存，审核问题：${r.issues.join('；')}` : 'AI 草稿已保存，等待人工审核'); }
  if (action === 'save-draft') { const r = await api(`/api/drafts/${id}`, 'PUT', {title:$('draft-title').value,description:$('draft-description').value}); toast(r.issues.length ? `已保存，审核问题：${r.issues.join('；')}` : '草稿已保存'); }
  if (action === 'approve-draft') { await api(`/api/drafts/${id}/approve`, 'POST'); toast('草稿已人工批准'); }
  if (action === 'task-verify') { const result=await api(`/api/browser/publish/${id}/verify`,'POST'); toast(result.note,result.state==='uncertain'); }
  else if (action.startsWith('task-')) { const status=action === 'task-posted' ? 'posted' : action === 'task-failed' ? 'failed' : 'needs_attention'; await api(`/api/tasks/${id}/result`,'POST',{status,marketplace_url:$(`task-url-${id}`).value,note:$(`task-note-${id}`).value}); toast('发布结果已记录'); }
  if (action === 'ai-reply') { if (!window.confirm('将这条询盘问题和商品字段发送到你配置的模型服务生成草稿。请先确认问题中没有买家联系方式等敏感信息。')) return; const r=await api(`/api/inquiries/${id}/reply-ai`,'POST',{allow_external_processing:true}); toast(`AI 回复草稿已保存，等待人工审核${r.review_notes.length ? '；待确认：'+r.review_notes.join('；') : ''}`); }
  if (['save-reply','approve-reply','sent-reply'].includes(action)) { const item=model.inquiries.find(i => i.id===id); const status=action === 'sent-reply' ? 'sent_manually' : action === 'approve-reply' ? 'approved' : 'draft'; await api(`/api/inquiries/${id}/reply`,'PUT',{reply_draft:$(`reply-${id}`).value,reply_status:status}); toast(status === 'sent_manually' ? '已记录手动发送' : status === 'approved' ? '回复已人工批准' : '回复草稿已保存'); }
  if (action === 'save-followup') { await api(`/api/inquiries/${id}/followup`,'PUT',{stage:$(`stage-${id}`).value,channel:$(`channel-${id}`).value,next_followup_at:$(`followup-${id}`).value || null}); toast('跟进状态已保存'); }
  await refresh();
  if (action === 'new-draft') $('draft-panel').scrollIntoView({behavior:'smooth',block:'center'});
}
document.addEventListener('click', async e => {
  const target=e.target.closest('[data-action]'); if (!target) return;
  if(target.dataset.action==='select-inbox-account'){
    selectedInboxAccountKey=target.dataset.accountKey;
    try { await withLoading(target,refreshVisibleConversations); }
    catch(err) { toast(err.message,true); }
    return;
  }
  try { if(target.dataset.action==='toggle-task') await perform(target.dataset.action,Number(target.dataset.id)); else await withLoading(target,()=>perform(target.dataset.action,Number(target.dataset.id))); } catch(err) { toast(err.message,true); }
});
document.addEventListener('error',e=>{if(e.target?.classList?.contains('account-avatar-img'))e.target.remove();},true);
document.addEventListener('change', async e => {
  if(e.target.id === 'draft-picker'){ selectedDraftId=Number(e.target.value); renderDraft(); return; }
  if(e.target.id === 'live-product'){aiDraftAdvice=null;const advice=$('live-ai-advice');if(advice)advice.innerHTML='';return;}
  if(e.target.id === 'account-chrome-default'){ selectedChromeAccount=e.target.checked; $('select-all-accounts').textContent=selectedChromeAccount?'取消全选':'全选账号'; return; }
  if(e.target.classList.contains('product-check')){ const id=Number(e.target.dataset.productId); e.target.checked?selectedProductIds.add(id):selectedProductIds.delete(id); return; }
  if(e.target.classList.contains('single-product-image')){const file=e.target.files[0];if(!file)return;try{await withLoading(e.target.closest('.file-button'),async()=>{await uploadProductImage(Number(e.target.dataset.productId),file);toast('商品图片已关联');await refresh();});}catch(err){toast(err.message,true);}return;}
});
async function fileBase64(file){const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));return btoa(binary);}
async function uploadProductImage(productId,file){if(file.size>12000000)throw new Error(`${file.name} 超过 12 MB`);return api(`/api/products/${productId}/image`,'POST',{filename:file.name,base64_data:await fileBase64(file)});}
$('csv-file').addEventListener('change', async e => {
  const file=e.target.files[0]; if(!file) return;
  try { await withLoading(e.target.closest('.file-button'),async()=>{
    let r;
    if(file.name.toLowerCase().endsWith('.xlsx')) { const bytes=new Uint8Array(await file.arrayBuffer()); let binary=''; for(let i=0;i<bytes.length;i+=32768) binary+=String.fromCharCode(...bytes.subarray(i,i+32768)); r=await api('/api/products/import-xlsx','POST',{base64_data:btoa(binary)}); }
    else r=await api('/api/products/import','POST',{csv_text:await file.text()});
    const box=$('import-report'); box.classList.remove('hidden'); box.textContent=`成功导入 ${r.imported} 行；错误 ${r.errors.length} 行。` + r.errors.map(x=>` 第 ${x.line} 行 (${x.sku || '无 SKU'}): ${x.messages.join('、')}`).join('');
    toast(`成功导入 ${r.imported} 件商品`); await refresh();
  }); } catch(err){toast(err.message,true);} finally {e.target.value='';}
});
$('product-images').addEventListener('change',async e=>{
  const files=[...e.target.files]; if(!files.length)return;
  try { await withLoading(e.target.closest('.file-button'),async()=>{
    let ok=0,miss=[];
    for(const file of files){const name=file.name.toLowerCase();const matches=model.products.filter(p=>name.startsWith(p.sku.toLowerCase()+'.')||name.startsWith(p.sku.toLowerCase()+'-')||name.startsWith(p.sku.toLowerCase()+'_'));if(matches.length!==1){miss.push(file.name);continue;}try{await uploadProductImage(matches[0].id,file);ok++;}catch(err){miss.push(`${file.name}: ${err.message}`);}}
    await refresh();toast(`已关联 ${ok} 张图片${miss.length?`；未匹配/失败：${miss.join('、')}`:''}`,!!miss.length);
  }); } catch(err){toast(err.message,true);} finally {e.target.value='';}
});
onBusyClick('clear-products',async()=>{
  const count=model.products.length;
  if(!count){toast('当前商品表已经是空的');return;}
  if(!window.confirm(`将清空当前商品表中的 ${count} 件商品。已有草稿和发布记录会保留为历史，重新导入相同 SKU 会建立新的商品记录。确定继续吗？`))return;
  const result=await api('/api/products/clear','POST',{confirm:true});
  selectedProductIds.clear();selectedDraftId=null;
  $('import-report').classList.add('hidden');
  await refresh();toast(`当前商品表已清空 ${result.cleared} 件，可重新导入`);
});
onBusyClick('start-batch',async()=>{const ids=[...selectedProductIds];if(!selectedChromeAccount)throw new Error('请在“在线账号”勾选 Chrome 账号');if(!ids.length)throw new Error('请勾选要上架的商品');const names=ids.map(id=>model.products.find(p=>p.id===id)?.sku).join('、');if(!window.confirm(`将在当前 Chrome 账号依次填写并尝试发布 ${ids.length} 件商品：${names}。请确认目标账号和商品内容。`))return;const r=await api('/api/browser/publish/start','POST',{product_ids:ids,account_keys:['chrome-default'],confirm:true});activeBatchTaskIds=new Set(r.task_ids);batchPolling=true;toast(`已建立 ${r.task_ids.length} 个网页上架任务`);await refresh();});
$('inquiry-form').addEventListener('submit', async e => { e.preventDefault(); try { await withLoading(e.currentTarget.querySelector('button[type="submit"]'),async()=>{await api('/api/inquiries','POST',{task_id:Number($('inquiry-task').value),buyer_alias:$('buyer-alias').value,question:$('question').value});$('buyer-alias').value='';$('question').value='';toast('询盘和回复草稿已建立');await refresh();}); } catch(err){toast(err.message,true);} });
onBusyClick('connect-browser',async()=>{toast('正在检查 Chrome 连接…');$('browser-detail').textContent='正在连接或读取已登录的 Facebook 账号…';setBrowserState(await api('/api/browser/connect','POST'));toast(browserState.state==='online'?'Chrome 已连接，账号已显示':'正在连接 Chrome；如出现授权弹窗，请点击允许');});
onBusyClick('refresh-account',async()=>{setBrowserState(await api('/api/browser/accounts/refresh','POST'));toast(browserState.state==='online'?'账号状态已刷新':'请检查 Chrome 登录与授权');});
onBusyClick('disconnect-browser',async()=>{setBrowserState(await api('/api/browser/disconnect','POST'));toast('已断开 Chrome 连接');});
$('select-all-accounts').addEventListener('click',()=>{if(browserState.state!=='online')return;selectedChromeAccount=!selectedChromeAccount;const checkbox=$('account-chrome-default');if(checkbox)checkbox.checked=selectedChromeAccount;$('select-all-accounts').textContent=selectedChromeAccount?'取消全选':'全选账号';});
onBusyClick('refresh-conversations',refreshVisibleConversations);
$('ai-provider').addEventListener('change',()=>{
  const url=$('ai-base-url');
  if(!url.value||['https://api.siliconflow.cn/v1','https://api.openai.com/v1'].includes(url.value))
    url.value=$('ai-provider').value==='openai'?'https://api.openai.com/v1':$('ai-provider').value==='siliconflow'?'https://api.siliconflow.cn/v1':'';
});
$('ai-settings-form').addEventListener('submit',async e=>{
  e.preventDefault();
  try { await withLoading($('save-ai-settings'),async()=>{
    aiConfig=await api('/api/ai/settings','PUT',{
      provider:$('ai-provider').value,base_url:$('ai-base-url').value,model:$('ai-model').value,
      api_key:$('ai-api-key').value,reply_system_prompt:$('ai-system-prompt').value,
    });
    renderAISettings();renderInbox();
    toast(aiConfig.configured?'AI 模型设置已保存并生效':'设置已保存；请填写当前服务的 API Key',!aiConfig.configured);
  }); } catch(err){toast(err.message,true);}
});
onBusyClick('reset-ai-settings',async()=>{
  if(!window.confirm('将删除网页保存的模型设置与密钥，恢复使用启动时的环境变量配置。确定继续吗？'))return;
  aiConfig=await api('/api/ai/settings','DELETE');
  renderAISettings();renderInbox();toast('已恢复环境变量配置');
});
onBusyClick('add-quick-reply',async()=>{
  const list=$('ai-quick-replies');
  list.querySelector('.empty')?.remove();
  if(list.querySelectorAll('.ai-quick-row').length>=20)throw new Error('常见话术最多 20 条');
  list.insertAdjacentHTML('beforeend',quickReplyHtml());
});
$('ai-quick-replies').addEventListener('click',e=>{
  const remove=e.target.closest('.ai-quick-remove');if(!remove)return;
  remove.closest('.ai-quick-row').remove();
  if(!$('ai-quick-replies').querySelector('.ai-quick-row'))$('ai-quick-replies').innerHTML='<div class="empty">尚未添加参考话术。</div>';
});
$('ai-knowledge-form').addEventListener('submit',async e=>{
  e.preventDefault();
  try{await withLoading($('save-ai-knowledge'),async()=>{
    const policies={};for(const key of ['shipping','pickup','payment','returns','negotiation'])policies[key]=$(`ai-policy-${key}`).value.trim();
    const quick_replies=[...$('ai-quick-replies').querySelectorAll('.ai-quick-row')].map(row=>({intent:row.querySelector('.ai-quick-intent').value,title:row.querySelector('.ai-quick-title-input').value.trim(),content:row.querySelector('.ai-quick-content-input').value.trim()}));
    aiKnowledge=await api('/api/ai/knowledge','PUT',{policies,quick_replies});renderAIKnowledge();toast('经营规则与常见话术已保存');
  });}catch(err){toast(err.message,true);}
});
(async()=>{const [aiResult,knowledgeResult,stateResult,browserResult]=await Promise.allSettled([api('/api/ai/status'),api('/api/ai/knowledge'),refresh(),loadBrowser()]);if(aiResult.status==='fulfilled')aiConfig=aiResult.value;if(knowledgeResult.status==='fulfilled')aiKnowledge=knowledgeResult.value;renderAISettings();renderAIKnowledge();renderInbox();if(aiResult.status==='rejected')toast(`AI 配置读取失败：${aiResult.reason.message}`,true);if(knowledgeResult.status==='rejected')toast(`经营规则读取失败：${knowledgeResult.reason.message}`,true);if(stateResult.status==='rejected')toast(`商品数据加载失败：${stateResult.reason.message}`,true);if(browserResult.status==='rejected')setBrowserState({state:'error',error:`账号状态读取失败：${browserResult.reason.message}`});})();
setInterval(async()=>{if(accountStatusPolling)return;accountStatusPolling=true;try{const previous=browserState.state;setBrowserState(previous==='no_facebook_tab'?await api('/api/browser/accounts/refresh','POST'):await api('/api/browser/status'));}catch{}finally{accountStatusPolling=false;}},4000);
setInterval(async()=>{if(!batchPolling)return;try{model=await api('/api/state');renderTasks();batchPolling=model.publish_tasks.some(t=>activeBatchTaskIds.has(t.id)&&['queued','preparing'].includes(t.status));if(!batchPolling){const failed=model.publish_tasks.find(t=>activeBatchTaskIds.has(t.id)&&!['posted','queued','preparing'].includes(t.status));const reviewing=model.publish_tasks.some(t=>activeBatchTaskIds.has(t.id)&&t.status==='posted'&&t.note?.includes('仍在审核'));activeBatchTaskIds.clear();toast(failed?`${productForTask(failed)?.sku||'商品'} 发布未完成：${failed.note||'请查看任务结果'}`:reviewing?'商品已提交发布，Facebook 正在审核':'本轮商品发布已完成',!!failed);}}catch{}},3500);
