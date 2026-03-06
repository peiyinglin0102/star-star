//workspaces/main/services/static/js/parent_timer.js
;(() => {
  const LS = window.localStorage;
  const ACTIVE_KEY = 'timer.active';
  const END_KEY    = 'timer.endAt';
  const TICK_MS    = 250;
  const warnKey = (phase, endAt) => `timer.warned.${phase}::${endAt}`;

  const getLogoutUrl = () => document.body?.getAttribute('data-logout-url') || '/auth/logout';

  // ★提醒門檻：小→大
const PHASES = [5, 30, 60]; // 小→大，邏輯 OK
const MESSAGES = {
  60: { speak: '聰明的小朋友，我們需要讓眼睛休息一下', toast: '我們需要讓眼睛休息一下 👀' },
  30: { speak: '小朋友你今天真的很棒，該結束練習了',   toast: '今天很棒，該結束練習了 ✨' },
   5: { speak: '小朋友，要關閉了，我們明天見',                 toast: '我們明天見！👋' },
};



  // Toast
  const ensureToast = () => {
    let t = document.getElementById('sysWarnToast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'sysWarnToast';
      t.className = 'sysset-toast';
      t.hidden = true;
      document.body.appendChild(t);
    }
    return t;
  };
  const showToast = (text, ms = 2200) => {
    const t = ensureToast();
    t.textContent = text;
    if ('hidden' in t) t.hidden = false; else t.style.display = 'block';
    setTimeout(() => { if ('hidden' in t) t.hidden = true; else t.style.display = 'none'; }, ms);
  };
// === Voice Pinning helpers (mobile will mirror desktop voice) ===
(function setupPinnedVoiceHelpers(){
  // 盡量找出同名/同 URI 的聲音
  window.getSavedDesktopVoice = function(){
    const vs = speechSynthesis.getVoices() || [];
    const uri  = localStorage.getItem('tts.voiceURI')  || '';
    const name = localStorage.getItem('tts.voiceName') || '';
    if (!uri && !name) return null;
    return vs.find(v =>
      (uri  && v.voiceURI === uri) ||
      (name && v.name     === name)
    ) || null;
  };

  // 若找不到同一把，做「桌機 → iOS」映射（語系優先，固定選女聲）
  window.mapDesktopToIOSVoice = function(){
    const vs = speechSynthesis.getVoices() || [];
    const pick = (reName, reLang=/^zh/i) =>
      vs.find(v => reName.test(v.name) || reName.test(v.voiceURI)) ||
      vs.find(v => reLang.test(v.lang || ''));

    const savedName = (localStorage.getItem('tts.voiceName') || '').toLowerCase();
    const savedURI  = (localStorage.getItem('tts.voiceURI') || '').toLowerCase();
    const key = (savedName + ' ' + savedURI);

    // Google 國語（臺灣） or zh-TW → iOS: Mei-Jia
    if (/google.*zh\-?tw|zh\-?tw/.test(key)) {
      return pick(/Mei[\-\s]?Jia|com\.apple\.ttsbundle\.Mei\-Jia/i, /zh\-?tw/i) || null;
    }
    // zh-CN → Ting-Ting
    if (/google.*zh|zh\-?cn/.test(key)) {
      return pick(/Ting[\-\s]?Ting|com\.apple\.ttsbundle\.Ting\-Ting/i, /zh/i) || null;
    }
    // zh-HK → Sin-Ji / Kaho
    if (/zh\-?hk|hong\s*kong|hk/.test(key)) {
      return pick(/Sin[\-\s]?Ji|Kaho|com\.apple\.ttsbundle\.(Sin\-Ji|Kaho)/i, /zh/i) || null;
    }
    // fallback：優先 zh-TW，再任何中文
    return vs.find(v => /zh\-?tw/i.test(v.lang || '')) ||
           vs.find(v => /^zh/i.test(v.lang || '')) || null;
  };

  // 在行動端「保底」：若能找到桌機那把 → 用；找不到 → 用映射
  window.pickPinnedMobileVoice = function(){
    return window.getSavedDesktopVoice() || window.mapDesktopToIOSVoice() || null;
  };
})();

  // 語音（可關閉）
  // —— 取代原有 window.StarSpeech 區塊 —— 
if (!window.StarSpeech) {
const IS_MOBILE = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);

const pickPreferredVoice = () => {
  const voices = (speechSynthesis?.getVoices?.() || []);

  // 只在行動裝置套用「啟動成功時選到的女聲」
  if (IS_MOBILE) {
    const savedURI  = localStorage.getItem('tts.voiceURI')  || '';
    const savedName = localStorage.getItem('tts.voiceName') || '';
    if (savedURI || savedName) {
      const v = voices.find(v =>
        (savedURI  && v.voiceURI === savedURI) ||
        (savedName && v.name     === savedName)
      );
      if (v) return v;
    }
  }

  // 桌機維持你原本的挑選邏輯（行動裝置若沒存到也走這裡）
  return (
    voices.find(v => /Google/i.test(v.name) && v.lang === 'zh-TW') ||
    voices.find(v => /Google/i.test(v.name) && /^zh[-_]/i.test(v.lang)) ||
    voices.find(v => v.lang === 'zh-TW') ||
    voices.find(v => /^zh[-_]/i.test(v.lang)) ||
    voices[0] || null
  );
};


  window.StarSpeech = {
    enabled: (localStorage.getItem('tts.enabled') ?? '1') !== '0',
    rate: Number(localStorage.getItem('tts.rate') ?? '1.0') || 1.0,
    voice: null,
    _queue: [],         // { text, delay, onEnd, tag, priority }
    _busy: false,
    _initVoiceOnce: false,

    ensureVoiceReady() {
      return new Promise((res) => {
        if (this._initVoiceOnce) return res();
        const pick = () => {
          this.voice = pickPreferredVoice();
          this._initVoiceOnce = true;
          res();
        };
        if ((speechSynthesis?.getVoices?.() || []).length) pick();
        else {
          const h = () => { pick(); speechSynthesis.removeEventListener('voiceschanged', h); };
          speechSynthesis.addEventListener('voiceschanged', h);
          speechSynthesis.getVoices(); // 觸發載入
        }
      });
    },

    // 放進佇列；priority: 'after'（等當前播完） / 'normal'
    enqueue({ text, delay = 0, onEnd = null, tag = '', priority = 'normal' } = {}) {
      if (!text || !this.enabled) {
        if (typeof onEnd === 'function') setTimeout(onEnd, Math.max(0, delay));
        return;
      }
      const item = { text: String(text), delay, onEnd, tag, priority };
      // 'after' 就放到隊尾；'normal' 也放隊尾（你之後若要插隊可加 'now' 策略）
      this._queue.push(item);
      this._pump();
    },

    // 舊接口相容
    async speak(text) { this.enqueue({ text }); },

    cancelAll() {
      try { speechSynthesis?.cancel?.(); } catch (_) {}
      this._queue.length = 0;
      this._busy = false;
    },

    async _pump() {
      if (this._busy) return;
      if (!this._queue.length) return;

      // 等待「目前沒有別人在講」
      if (speechSynthesis?.speaking) {
        // 每 120ms 檢查一次，直到空檔
        const checker = setInterval(() => {
          if (!speechSynthesis.speaking) {
            clearInterval(checker);
            this._pump();
          }
        }, 120);
        return;
      }

      const job = this._queue.shift();
      this._busy = true;

      await this.ensureVoiceReady();

      const u = new SpeechSynthesisUtterance(job.text);
      if (this.voice) { u.voice = this.voice; if (this.voice.lang) u.lang = this.voice.lang; }
      else { u.lang = 'zh-TW'; }
      u.rate = Math.min(2, Math.max(0.5, this.rate));
      u.pitch = 1.0;

      u.onend = () => {
        try { if (typeof job.onEnd === 'function') job.onEnd(); } catch(_) {}
        this._busy = false;
        // 為了避免和 quiz 的 onend 交錯，稍等一下再接下一個
        setTimeout(() => this._pump(), 120);
      };
      u.onerror = () => {
        this._busy = false;
        setTimeout(() => this._pump(), 120);
      };

      // 重要：不要在這裡 cancel()，避免把 quiz 的語音砍掉
      setTimeout(() => { try { speechSynthesis.speak(u); } catch(_) { this._busy = false; } }, Math.max(0, job.delay));
    }
  };
}

// —— 將原本 maybeWarn 裡的 speak(m.speak) 改成 enqueue（等題目唸完再提醒）——
 const speak = msg => window.StarSpeech?.enqueue?.({
   text: msg,
   tag: 'parent-timer',
   priority: 'after',   // 一律等題目播完
   delay: 0
});

  // 後端同步（可有可無；為了 server-side 保險）
  const syncStart = (endAt) => fetch('/timer/start', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ end_at_ms: endAt }) }).catch(()=>{});
  const syncStop  = () => fetch('/timer/stop',  { method:'POST', headers:{'Content-Type':'application/json'} }).catch(()=>{});
// ========== 狀態 ==========
  const nnum = v => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
  const status = () => {
    const active = LS.getItem(ACTIVE_KEY) === '1';
    const endAt  = nnum(LS.getItem(END_KEY));
    const left   = (active && endAt > 0) ? Math.max(0, endAt - Date.now()) : 0;
    return { active: (active && left > 0), left, endAt };
  };
  const setActive   = (endAt) => { LS.setItem(ACTIVE_KEY, '1'); LS.setItem(END_KEY, String(endAt)); };
  const clearActive = () => { LS.removeItem(ACTIVE_KEY); LS.removeItem(END_KEY); };

  // 門檻提醒
  const maybeWarn = (leftMs, endAt) => {
    if (!endAt || leftMs <= 0) return;
    const secs = Math.ceil(leftMs / 1000);
    for (let i = 0; i < PHASES.length; i++){
      const p = PHASES[i];
      if (secs <= p) {
        const k = warnKey(p, endAt);
        if (LS.getItem(k) !== '1') {
          LS.setItem(k, '1');
          const m = MESSAGES[p] || {};
          if (m.speak) speak(m.speak);
          if (m.toast) showToast(m.toast, 3000);
        }
        break;
      }
    }
  };
 // ========== 倒數主循環 + 硬切換 ==========
  let tickTimer = null;
  let killTimer = null; // ★ fail-safe：到點一定硬切到登出

  const hardKillAt = (endAt) => {
    if (killTimer) clearTimeout(killTimer);
    const dur = Math.max(0, endAt - Date.now());
    killTimer = setTimeout(() => {
  try { clearActive(); } catch(_) {}
  try { speechSynthesis?.cancel?.(); } catch(_) {}
  // ★ 到點：若頁面要求延後，僅標記 pending，不立刻跳轉
  try { LS.setItem('timer.logoutPending', '1'); } catch(_) {}
  if (window.DeferLogout) {
    // 延後到該回合結束由 quiz 轉導
  } else {
    location.replace(getLogoutUrl());
  }
}, dur + 250);

  };
const tick = () => {
  const st = status();
  if (st.active) {
    try { maybeWarn(st.left, st.endAt); } catch(e) { console.warn('warn err', e); }
    if (st.left <= 0) {
      try { clearActive(); } catch(_) {}
      try { speechSynthesis?.cancel?.(); } catch(_){}
      location.replace(getLogoutUrl());
      return;
    }
    return; // 繼續倒數
  }
  if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
};

  // ========== 對外 API ==========
  const ParentTimer = {
    start(durationMs){
      const dur = Math.max(0, (durationMs|0));
      if (!dur) { alert('請輸入大於 0 的時間'); return; }
      const endAt = Date.now() + dur;
      setActive(endAt);

      // 啟動輪詢 + 硬切換（雙保險）
      if (!tickTimer) tickTimer = setInterval(tick, TICK_MS);
      tick();
      hardKillAt(endAt);

      // 跨分頁同步
      LS.setItem('timer.ping', String(Math.random()));
    },
    stop(){
      clearActive();
      if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
      if (killTimer) { clearTimeout(killTimer); killTimer = null; }
      LS.setItem('timer.ping', String(Math.random()));
    },
    status
  };
  window.ParentTimer = ParentTimer;

  // ========== 初次載入：如果已在倒數，立刻接管 ==========
  (() => {
    const st = status();
    if (LS.getItem(ACTIVE_KEY) === '1') {
      if (st.left <= 0) {
        // 直接登出（避免上一頁設完時間，這頁才打開）
        try { clearActive(); } catch(_) {}
        location.replace(getLogoutUrl());
      } else {
        hardKillAt(st.endAt);
        tickTimer = setInterval(tick, TICK_MS);
        tick();
      }
    }
  })();

  // ========== 跨分頁同步 ==========
  window.addEventListener('storage', (e) => {
    if (!e.key) return;
    if (e.key === ACTIVE_KEY || e.key === END_KEY || e.key === 'timer.ping') {
      if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
      const st = status();
      if (LS.getItem(ACTIVE_KEY) === '1') {
        if (st.left <= 0) {
          try { clearActive(); } catch(_) {}
          location.replace(getLogoutUrl());
          return;
        }
        hardKillAt(st.endAt);
        tickTimer = setInterval(tick, TICK_MS);
        tick();
      } else {
        if (killTimer) { clearTimeout(killTimer); killTimer = null; }
      }
    }
  });

  // ========== 點擊「登出」按鈕時先清狀態 ==========
  document.addEventListener('click', (e) => {
    const a = e.target?.closest?.('a,button');
    if (!a) return;
    const href = a.getAttribute?.('href') || '';
    if (/\/auth\/logout$/.test(href)) {
      ParentTimer.stop();
    }
  }, true);
  // ====== （可選）UI 綁定：有 #timeRemain / #timerStart / #timerStop 就會自動控制 ======
  const $ = (s) => document.querySelector(s);
  const fmt = (ms) => {
    if (ms <= 0) return '00:00';
    const s = Math.floor(ms/1000), m = Math.floor(s/60);
    return String(m).padStart(2,'0') + ':' + String(s%60).padStart(2,'0');
  };
  const refreshRemain = () => {
    const { active, left } = ParentTimer.status();
    const remainEl = $('#timeRemain');
    const btnStart = $('#timerStart');
    const btnStop  = $('#timerStop');
    if (remainEl) remainEl.textContent = active ? fmt(left) : '未開始';
    if (btnStart) btnStart.disabled = !!active;
    if (btnStop)  btnStop.disabled  = !active;
  };
  setInterval(refreshRemain, 500);
  document.addEventListener('visibilitychange', ()=>{ if (!document.hidden) refreshRemain(); });
  window.addEventListener('storage', (e)=>{ if (['timer.active','timer.endAt','timer.ping'].includes(e.key||'')) refreshRemain(); });
  refreshRemain();
})();
// 🔁 覆寫 safeSpeak（行動端）：不 cancel()、用與桌機一致的 pinned voice
const oldSafeSpeak = window.safeSpeak;
window.safeSpeak = async function mobileSafeSpeak(text, delay = 0, onEnd = null) {
  const IS_MOBILE = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
  if (!IS_MOBILE) return oldSafeSpeak ? oldSafeSpeak(text, delay, onEnd) : null;
  if (!text) return;

  // 等 voices 準備好
  const ok = () => (speechSynthesis.getVoices() || []).length > 0;
  if (!ok()) {
    await new Promise(r => {
      const kick = () => { try { speechSynthesis.getVoices(); } catch(_){} };
      let n = 0; (function tick(){ if (ok() || n++>20) return r(); kick(); setTimeout(tick, 100); })();
    });
  }
  try { await window.StarSpeech?.ensureVoiceReady?.(); } catch(_) {}

  // ✨重點：iOS/Android 一律使用與桌機相同（或對應）的聲音
  const pinned = window.pickPinnedMobileVoice?.();
  const u = new SpeechSynthesisUtterance(text);
  if (pinned) { u.voice = pinned; if (pinned.lang) u.lang = pinned.lang; }
  else {
    // 最後保底：沿用你原本的統一入口
    const v = (typeof getUnifiedVoice === 'function') ? getUnifiedVoice() : null;
    if (v) { u.voice = v; if (v.lang) u.lang = v.lang; } else { u.lang = 'zh-TW'; }
  }

  const rate = parseFloat(localStorage.getItem('tts.rate') || '1') || 1;
  u.rate = Math.min(2, Math.max(0.5, rate));
  u.pitch = 1.0;
  u.onend = () => { try { onEnd && onEnd(); } catch(_) {} };

  // 行動端：播放前不呼叫 cancel()，避免砍掉第一字
  setTimeout(() => { try { speechSynthesis.speak(u); } catch(_) {} }, Math.max(0, delay));
};
