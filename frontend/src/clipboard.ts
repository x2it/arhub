/** 剪贴板工具：复制文本 / 复制图片，带非安全上下文（HTTP 内网）降级。
 *
 * 内网反代常是 HTTP，navigator.clipboard 仅在安全上下文（HTTPS/localhost）可用，
 * 因此：文本走 execCommand 兜底；图片在剪贴板不可用时由调用方降级为下载。
 * 所有函数都不抛异常，返回布尔 / 状态，由调用方决定提示文案。 */

/** 复制纯文本。优先 navigator.clipboard（安全上下文），否则临时 textarea + execCommand。 */
export async function copyText(text: string): Promise<boolean> {
  if (!text) return false
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* 落到兜底分支 */
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.top = '-1000px'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    ta.remove()
    return ok
  } catch {
    return false
  }
}

/** 将已就绪的 PNG Blob 写入系统剪贴板。成功返回 true；不可用或失败返回 false。
 *  调用方应据此降级（如直接下载图片）。 */
export async function copyImageBlob(blob: Blob): Promise<boolean> {
  try {
    if (
      navigator.clipboard &&
      typeof (navigator.clipboard as any).write === 'function' &&
      window.isSecureContext &&
      typeof ClipboardItem !== 'undefined'
    ) {
      await (navigator.clipboard as any).write([new ClipboardItem({ 'image/png': blob })])
      return true
    }
  } catch {
    /* 落到调用方降级 */
  }
  return false
}

/** 把二进制 Blob 转成 PNG Blob（剪贴板只稳妥接受 image/png，而预览接口返回 JPEG）。 */
export function blobToPng(blob: Blob): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob)
    const img = new Image()
    img.onload = () => {
      try {
        const canvas = document.createElement('canvas')
        canvas.width = img.naturalWidth
        canvas.height = img.naturalHeight
        const ctx = canvas.getContext('2d')
        if (!ctx) throw new Error('画布不可用')
        ctx.drawImage(img, 0, 0)
        canvas.toBlob((b) => {
          URL.revokeObjectURL(url)
          if (b) resolve(b)
          else reject(new Error('图片转换失败'))
        }, 'image/png')
      } catch (e: any) {
        URL.revokeObjectURL(url)
        reject(e)
      }
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('图片解码失败'))
    }
    img.src = url
  })
}

/** 通用 Blob 下载（不依赖鉴权头，适用于本地已持有的二进制）。 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 30_000)
}
