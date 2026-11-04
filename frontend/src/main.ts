import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import './assets/main.css'

import LoginView from './views/Login.vue'
import CaptureView from './views/Capture.vue'
import DocumentListView from './views/DocumentList.vue'
import DocumentDetailView from './views/DocumentDetail.vue'
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
    { path: '/acl', component: AccessControlView },
    { path: '/audit', component: AuditLogView },
    { path: '/dashboard', component: DashboardView },
    { path: '/ops', component: OpsView },
  ],
})

// 全局路由守卫：未登录跳登录页（后端仍是唯一裁决者）
router.beforeEach((to) => {
  const token = localStorage.getItem('archive_jwt')
  if (to.path !== '/login' && !token) return '/login'
  if (to.path === '/login' && token) return '/'
})

createApp(App).use(router).mount('#app')
