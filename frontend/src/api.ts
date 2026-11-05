/** 同源 API 客户端：JWT 三通道冗余 + 请求 ID + 401 统一登出回跳。
 *
 * 公共发布平台会占用 Authorization 头做自身访问控制，因此额外用
 * X-Arc-Token 与 cookie 双通道携带，后端按 X-Arc-Token > cookie > Authorization
 * 的可信度顺序取令牌。所有请求（含二进制/流式）都必须经过 authFetch，
 * 否则 401 不会触发统一登出，页面会「看起来正常但全部操作失败」。 */
const RID = () => Math.random().toString(36).slice(2) + Date.now().toString(36)
const COOKIE = 'arc_jwt'
const TOKEN_KEY = 'archive_jwt'
const USER_KEY = 'archive_user'
const REDIRECT_KEY = 'arc_redirect'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

/** 解析 JWT 的 exp 判断登录态。畸形或已过期一律视为未登录，
 *  避免「localStorage 里还有过期 token → 路由守卫放行 → 所有接口 401」的死局。 */
export function isTokenValid(): boolean {
  const t = getToken()
  if (!t) return false
  try {
    const b64 = t.split('.')[1]?.replace(/-/g, '+').replace(/_/g, '/')
    if (!b64) return false
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4)
    const payload = JSON.parse(atob(padded))
    // 预留 5 秒边界，避免令牌在请求途中恰好过期
    return typeof payload.exp === 'number' && payload.exp * 1000 > Date.now() + 5000
  } catch {
    return false
  }
}

/** 统一的鉴权请求头：所有 fetch 调用都应带上，避免被平台反代覆盖后 401。 */
export function authHeaders(): Record<string, string> {
  const t = isTokenValid() ? getToken() : ''
  return t ? { Authorization: `Bearer ${t}`, 'X-Arc-Token': t } : {}
}

export function getUser(): any {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null')
  } catch {
    return null
  }
}

export function setAuth(token: string, user: any, expiresIn = 43200) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
  // cookie 通道：<a href> / <img src> 等原生请求无法带自定义头，只能靠 cookie。
  // 有效期与后端 expires_in 对齐（默认 12h），否则 cookie 会先于 JWT 失效。
  document.cookie = `${COOKIE}=${token}; path=/; SameSite=Lax; max-age=${Math.max(60, Number(expiresIn) || 43200)}`
}

export function logout() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
  document.cookie = `${COOKIE}=; path=/; SameSite=Lax; max-age=0`
}

export function hasRole(...roles: string[]): boolean {
  const u = getUser()
  if (!u) return false
  return (u.roles || []).some((r: string) => roles.includes(r))
}

/** 统一解析后端错误：{error:{code,message}} / {detail} / 纯文本 / HTML 截断。 */
export async function readError(res: Response): Promise<string> {
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) {
    const d = await res.json().catch(() => null)
    return d?.error?.message || d?.detail || d?.message || `请求失败 HTTP ${res.status}`
  }
  const text = (await res.text().catch(() => '')).replace(/<[^>]*>/g, ' ').trim()
  return text.slice(0, 160) || `请求失败 HTTP ${res.status}`
}

/** 登录态失效的专属错误：调用方可据此跳过「业务失败降级」逻辑，
 *  避免已经跳到登录页了还在提示「将按原图归档」这类误导信息。 */
export class AuthError extends Error {}

/** 所有请求的唯一出口：三通道鉴权 + 请求 ID；401 统一登出并回跳登录页。
 *  返回原始 Response，便于调用方处理 blob / 流式下载。 */
export async function authFetch(path: string, opts: RequestInit = {}): Promise<Response> {
  const headers: Record<string, string> = {
    'X-Request-ID': RID(),
    ...authHeaders(),
    ...((opts.headers as Record<string, string>) || {}),
  }
  // URLSearchParams / 字符串表单默认按 form-urlencoded 发送；不覆盖调用方显式指定的类型。
  if (opts.body && !headers['Content-Type'] &&
      !(opts.body instanceof FormData) && !(opts.body instanceof Blob)) {
    headers['Content-Type'] = 'application/x-www-form-urlencoded'
  }
  const res = await fetch(path, { ...opts, headers })
  if (res.status === 401) {
    logout()
    const cur = location.hash
    if (cur && !cur.includes('login')) sessionStorage.setItem(REDIRECT_KEY, cur)
    if (!location.hash.includes('login')) location.hash = '#/login'
    throw new AuthError('登录已过期或尚未登录，请重新登录')
  }
  return res
}

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await authFetch(path, opts)
  const ct = res.headers.get('content-type') || ''
  if (!ct.includes('application/json')) {
    if (!res.ok) throw new Error(await readError(res))
    return res as unknown as T
  }
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data?.error?.message || data?.detail || `HTTP ${res.status}`)
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
  if (!res.ok) throw new Error(data?.error?.message || data?.detail || '登录失败')
  setAuth(data.access_token, data.user, data.expires_in)
  return data.user
}

/** 取回并清除 401 前所在页面，登录成功后原路返回。 */
export function consumeRedirect(): string | null {
  const v = sessionStorage.getItem(REDIRECT_KEY)
  sessionStorage.removeItem(REDIRECT_KEY)
  return v && v.startsWith('#/') ? v.slice(1) : null
}

export const CLASS_NAMES = ['公开', '内部', '秘密', '机密']
