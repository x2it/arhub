<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getUser, hasRole, logout } from './api'
import BrandMark from './components/BrandMark.vue'

const route = useRoute()
const router = useRouter()
// getUser() 读的是 localStorage，本身非响应式；显式依赖 route.path，
// 登录跳转后才会重新求值，否则顶栏用户名永远为空。
const user = computed(() => {
  void route.path
  return getUser()
})

const nav = computed(() => {
  const items = [
    { path: '/', label: '档案检索', show: true },
    { path: '/capture', label: '采集扫描', show: true },
    { path: '/dashboard', label: '统计', show: true },
    { path: '/acl', label: '权限', show: hasRole('admin') },
    { path: '/audit', label: '审计', show: hasRole('admin', 'auditor') },
    { path: '/ops', label: '运维', show: hasRole('admin') },
  ]
  return items.filter((i) => i.show)
})

function doLogout() {
  logout()
  router.push('/login')
}
</script>

<template>
  <div v-if="route.path !== '/login'">
    <header class="topbar">
      <div class="layout bar-inner">
        <span class="brand">
          <BrandMark :size="30" />
          <span class="brand-name">档枢 <b>ArcHub</b></span>
          <span class="brand-tag">让每一份档案，都可被找到</span>
        </span>
        <nav class="row">
          <router-link
            v-for="item in nav"
            :key="item.path"
            :to="item.path"
            class="nav-link"
            :class="{ on: route.path === item.path }"
            >{{ item.label }}</router-link
          >
        </nav>
        <span class="row user-box">
          <span class="muted">{{ user?.username }}</span>
          <button class="btn" @click="doLogout">退出</button>
        </span>
      </div>
    </header>
  </div>
  <router-view />
</template>

<style scoped>
.topbar {
  background: linear-gradient(100deg, var(--ink) 0%, var(--ink-2) 100%);
  border-bottom: 2px solid var(--gold);
  margin-bottom: 18px;
  box-shadow: 0 2px 12px rgba(14, 42, 71, 0.12);
}
.bar-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding-top: 10px;
  padding-bottom: 10px;
}
.brand { display: inline-flex; align-items: center; gap: 10px; color: #fff; }
.brand-name { font-weight: 700; font-size: 17px; letter-spacing: 0.04em; white-space: nowrap; }
.brand-name b { font-weight: 500; opacity: 0.8; font-size: 14px; letter-spacing: 0.06em; }
.brand-tag {
  font-size: 12px; color: #B9CDDF; border-left: 1px solid rgba(255, 255, 255, 0.25);
  padding-left: 10px; white-space: nowrap;
}
.nav-link { padding: 6px 11px; border-radius: 8px; color: #D5E3F0; font-size: 13px; }
.nav-link:hover { color: #fff; background: rgba(255, 255, 255, 0.09); }
.nav-link.on { color: #fff; background: rgba(198, 154, 75, 0.22); font-weight: 600; box-shadow: inset 0 -2px 0 var(--gold); }
.user-box .muted { color: #B9CDDF; }
.user-box .btn { background: rgba(255, 255, 255, 0.12); border-color: rgba(255, 255, 255, 0.22); color: #fff; }
.user-box .btn:hover { background: rgba(255, 255, 255, 0.2); }
@media (max-width: 860px) { .brand-tag { display: none; } }
</style>
