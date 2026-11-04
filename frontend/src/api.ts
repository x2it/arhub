/** 同源 API 客户端：JWT 三通道冗余 + 请求ID + 401 跳登录
 *  公共发布平台会占用 Authorization 头，因此额外用 X-Arc-Token 与 cookie 双通道携带。 */
const RID = () => Math.random().toString(36).slice(2) + Date.now().toString(36)
const COOKIE = 'arc_jwt'

export function getToken(): string {
  return localStorage.getItem('archive_jwt') || ''
}

/** 统一的鉴权请求头：所有 fetch 调用都应带上，避免被平台反代覆盖后 401。 */
export function authHeaders(): Record<string, string> {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}`, 'X-Arc-Token': t } : {}
}

export function getUser(): any {
  try {
    return JSON.parse(localStorage.getItem('archive_user') || 'null')
  } catch {
    return null
  }
}

export function setAuth(token: string, user: any) {
  localStorage.setItem('archive_jwt', token)
  localStorage.setItem('archive_user', JSON.stringify(user))
  // cookie 通道：<a href> / <img src> 等原生请求无法带自定义头，只能靠 cookie
  document.cookie = `${COOKIE}=${token}; path=/; SameSite=Lax; max-age=28800`
}

export function logout() {
  localStorage.removeItem('archive_jwt')
  localStorage.removeItem('archive_user')
  document.cookie = `${COOKIE}=; path=/; SameSite=Lax; max-age=0`
}

export function hasRole(...roles: string[]): boolean {
  const u = getUser()
  if (!u) return false
  return (u.roles || []).some((r: string) => roles.includes(r))
}

export async function api<T = any>(
  path: string,
  opts: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    'X-Request-ID': RID(),
    ...authHeaders(),
    ...(opts.headers as Record<string, string>),
  }
  if (!(opts.body instanceof FormData) && opts.body) {
    headers['Content-Type'] = 'application/x-www-form-urlencoded'
  }
  const res = await fetch(path, { ...opts, headers })
  if (res.status === 401) {
    logout()
    if (!location.hash.includes('login')) location.hash = '#/login'
    throw new Error('登录已过期，请重新登录')
  }
  const ct = res.headers.get('content-type') || ''
  if (!ct.includes('application/json')) {
    if (!res.ok) throw new Error(`请求失败 HTTP ${res.status}`)
    return res as unknown as T
  }
  const data = await res.json()
  if (!res.ok) {
    const msg = data?.error?.message || data?.detail || `HTTP ${res.status}`
    throw new Error(msg)
  }
  return data as T
}

export async function login(username: string, password: string) {
  const body = new URLSearchParams({ username, password })
  const res = await fetch('/api/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data?.detail || '登录失败')
  setAuth(data.access_token, data.user)
  return data.user
}

export const CLASS_NAMES = ['公开', '内部', '秘密', '机密']
