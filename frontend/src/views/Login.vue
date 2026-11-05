<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { consumeRedirect, login } from '../api'
import BrandMark from '../components/BrandMark.vue'

const router = useRouter()
const username = ref('')
const password = ref('')
const err = ref('')
const busy = ref(false)

async function submit() {
  err.value = ''
  if (!username.value || !password.value) {
    err.value = '请输入用户名和密码'
    return
  }
  busy.value = true
  try {
    await login(username.value, password.value)
    // 登录过期被踢回登录页时，成功后回到原来正在操作的页面
    router.push(consumeRedirect() || '/')
  } catch (e: any) {
    err.value = e.message || '登录失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <div class="brand-panel">
      <div class="panel-inner">
        <div class="panel-logo">
          <BrandMark :size="46" />
          <div>
            <div class="panel-name">档枢 <b>ArcHub</b></div>
            <div class="panel-sub">智能档案中枢 · INTELLIGENT ARCHIVE HUB</div>
          </div>
        </div>
        <h1 class="panel-slogan">让每一份档案，<br />都可被找到。</h1>
        <p class="panel-desc">
          从纸质扫描到语义检索的一体化平台。密级管控、全量审计、私有化部署，
          让档案既安全，又触手可及。
        </p>
        <ul class="feature-list">
          <li><span class="dot"></span>拍照自动增强 · 裁边 / 透视校正 / 去阴影</li>
          <li><span class="dot"></span>OCR 著录 · 档号、元数据、全文可检索</li>
          <li><span class="dot"></span>语义检索 · 用大白话也能找到目标档案</li>
          <li><span class="dot"></span>密级 + RBAC + 全量审计 · 越权零容忍</li>
        </ul>
        <div class="panel-foot">ARChub v2.0 · 单机内网私有化部署</div>
      </div>
    </div>

    <div class="form-panel">
      <div class="login-stack">
        <!-- 移动端精简品牌头：品牌区在窄屏隐藏后，页面顶部不再是一大片空白 -->
        <div class="mobile-brand">
          <BrandMark :size="34" />
          <div class="mobile-brand-text">
            <b>档枢 <span>ArcHub</span></b>
            <small>智能档案中枢 · 让每一份档案都可被找到</small>
          </div>
        </div>
        <form class="card login-card" @submit.prevent="submit">
        <div class="form-head">
          <BrandMark :size="28" />
          <h2>安全登录</h2>
        </div>
        <p class="muted form-tip">请使用管理员分配的账号访问档案库</p>
        <label class="field-label">用户名</label>
        <input v-model="username" placeholder="请输入用户名" autocomplete="username" style="width: 100%" />
        <label class="field-label" style="margin-top: 14px">密码</label>
        <input
          v-model="password"
          type="password"
          placeholder="请输入密码"
          autocomplete="current-password"
          style="width: 100%"
        />
        <p v-if="err" class="err">{{ err }}</p>
        <button class="btn primary" style="width: 100%; margin-top: 20px; padding: 10px" :disabled="busy">
          <span v-if="busy" class="spinner" style="border-top-color: #fff"></span>
          登 录
        </button>
        <div class="form-foot">档枢 ArcHub · 所有访问均被记录与审计</div>
        </form>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: 100vh;
  min-height: 100dvh; /* 移动端浏览器地址栏收起/展开时按可视高度算，避免大片空白 */
  display: grid;
  /* minmax(0,…) 是关键：默认 1fr 的最小尺寸是 auto，会被右侧固定宽卡片撑成
     424px，在 375px 视口下右边直接被裁掉（即用户截图里的登录区问题）。 */
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 0.95fr);
}
/* ---------- 左：品牌叙事 ---------- */
.brand-panel {
  position: relative;
  overflow: hidden;
  background: linear-gradient(145deg, #0A2138 0%, #123A5E 55%, #17507C 100%);
  color: #fff;
  display: flex;
  align-items: center;
}
.brand-panel::before {
  content: '';
  position: absolute;
  width: 520px; height: 520px;
  right: -180px; top: -160px;
  border-radius: 50%;
  border: 1px solid rgba(198, 154, 75, 0.25);
  box-shadow: 0 0 0 60px rgba(198, 154, 75, 0.05), 0 0 0 130px rgba(198, 154, 75, 0.035);
}
.brand-panel::after {
  content: '';
  position: absolute;
  width: 260px; height: 260px;
  left: -120px; bottom: -110px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(14, 107, 168, 0.35), transparent 70%);
}
.panel-inner { position: relative; z-index: 1; padding: 56px clamp(32px, 6vw, 88px); max-width: 620px; }
.panel-logo { display: flex; align-items: center; gap: 14px; }
.panel-name { font-size: 22px; font-weight: 700; letter-spacing: 0.05em; }
.panel-name b { font-weight: 500; opacity: 0.78; font-size: 17px; }
.panel-sub { font-size: 11px; letter-spacing: 0.16em; color: #9FBBD4; margin-top: 4px; }
.panel-slogan {
  font-size: clamp(30px, 3.4vw, 46px);
  line-height: 1.35;
  margin: 54px 0 18px;
  font-weight: 700;
  letter-spacing: 0.02em;
}
.panel-slogan::after {
  content: ''; display: block; width: 54px; height: 3px;
  background: var(--gold); margin-top: 22px; border-radius: 2px;
}
.panel-desc { color: #C2D5E6; line-height: 1.9; font-size: 14px; max-width: 430px; margin: 0 0 30px; }
.feature-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; }
.feature-list li { display: flex; align-items: center; gap: 11px; color: #E3EEF7; font-size: 13px; }
.dot {
  width: 6px; height: 6px; border-radius: 50%; background: var(--gold);
  flex: none; box-shadow: 0 0 0 4px rgba(198, 154, 75, 0.16);
}
.panel-foot { margin-top: 56px; font-size: 11px; letter-spacing: 0.1em; color: #7FA0BC; }
/* ---------- 右：登录表单 ---------- */
.form-panel {
  display: flex; align-items: center; justify-content: center;
  background: var(--bg); padding: 32px;
  min-width: 0;
}
.login-stack {
  width: 100%;
  max-width: 360px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.login-card { width: 100%; max-width: 100%; padding: 34px 32px; margin: 0; }
.mobile-brand { display: none; align-items: center; gap: 10px; padding: 0 2px; }
.mobile-brand-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.mobile-brand-text b { font-size: 17px; color: var(--ink); }
.mobile-brand-text b span { font-weight: 500; opacity: .75; font-size: 14px; }
.mobile-brand-text small { font-size: 11px; color: var(--muted); }
.form-head { display: flex; align-items: center; gap: 10px; }
.form-head h2 { font-size: 20px; margin: 0; }
.form-tip { font-size: 12px; margin: 10px 0 26px; }
.field-label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 6px; }
.btn.primary { font-weight: 600; letter-spacing: 0.08em; }
.form-foot {
  margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--border);
  text-align: center; font-size: 11px; color: var(--muted);
}
.err { color: var(--danger); font-size: 13px; margin: 10px 0 0; }
@media (max-width: 820px) {
  .login-wrap { grid-template-columns: minmax(0, 1fr); min-height: 100dvh; }
  .brand-panel { display: none; }
  .form-panel {
    min-height: 100vh;
    min-height: 100dvh;
    align-items: flex-start; /* 顶部对齐，不再把卡片垂直推到屏幕中段留一大片空白 */
    padding: calc(28px + var(--safe-top)) 16px calc(28px + var(--safe-bottom));
  }
  .login-stack { max-width: 420px; }
  .login-card { padding: 26px 20px; }
  .mobile-brand { display: flex; }
}
@media (max-width: 360px) {
  .form-panel { padding-left: 12px; padding-right: 12px; }
  .login-card { padding: 22px 16px; }
}
</style>
