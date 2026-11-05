import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import { isTokenValid } from './api'
import './assets/main.css'

import LoginView from './views/Login.vue'
import CaptureView from './views/Capture.vue'
import DocumentListView from './views/DocumentList.vue'
import DocumentDetailView from './views/DocumentDetail.vue'
import PreviewView from './views/PreviewView.vue'
import AccessControlView from './views/AccessControl.vue'
import AuditLogView from './views/AuditLog.vue'
import DashboardView from './views/Dashboard.vue'
import OpsView from './views/Ops.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', component: LoginView },
    { path: '/', component: DocumentListView },
    { path: '/capture', component: CaptureView },
    { path: '/doc/:id', component: DocumentDetailView },
    { path: '/preview/:id', component: PreviewView },
    { path: '/acl', component: AccessControlView },
    { path: '/audit', component: AuditLogView },
    { path: '/dashboard', component: DashboardView },
    { path: '/ops', component: OpsView },
  ],
})

// 全局路由守卫：不仅判断 token 是否存在，还要校验 exp 是否仍然有效。
// 只判断字符串存在会让「已过期令牌」放行，页面照常渲染但所有接口 401，
// 用户会卡在一个看似正常、实则完全不可用的界面里。后端仍是唯一裁决者。
router.beforeEach((to) => {
  const valid = isTokenValid()
  if (to.path !== '/login' && !valid) {
    if (to.fullPath !== '/') sessionStorage.setItem('arc_redirect', '#' + to.fullPath)
    return '/login'
  }
  if (to.path === '/login' && valid) return '/'
})

createApp(App).use(router).mount('#app')
