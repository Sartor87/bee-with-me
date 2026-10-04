import axios from 'axios'

// What every failed request rejects with. `message` and `detail` carry what callers used to get
// as a bare string (the response `detail`, or the axios message), `status` is the HTTP status
// (undefined when no response arrived: network error, timeout, abort). Compare with `.detail`.
export class ApiError extends Error {
  constructor(detail, status) {
    const text = Array.isArray(detail)
      ? detail.map(d => d?.msg ?? String(d)).join('; ')
      : String(detail ?? '')
    super(text)
    this.name = 'ApiError'
    this.detail = detail
    this.status = status
  }
}

// Text for display from anything a catch block can receive.
export function errorText(e, fallback = '') {
  if (typeof e === 'string') return e
  return e?.message || fallback
}

// The `detail` of a rejection, whether it is an ApiError or a bare string.
export function detailOf(e) {
  return typeof e === 'string' ? e : e?.detail
}

function toApiError(err) {
  return new ApiError(err.response?.data?.detail ?? err.message, err.response?.status)
}

const api = axios.create({ baseURL: '/api' })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

let isRefreshing = false
let refreshQueue = []

api.interceptors.response.use(
  (r) => (r.config?.fullResponse ? r : r.data),
  async (err) => {
    const original = err.config

    if (err.response?.status === 401 && !original._retry) {
      original._retry = true
      const refreshToken = localStorage.getItem('refresh_token')

      if (!refreshToken) {
        localStorage.removeItem('token')
        window.location.href = '/login'
        return Promise.reject(toApiError(err))
      }

      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          refreshQueue.push({ resolve, reject })
        }).then((newToken) => {
          original.headers.Authorization = `Bearer ${newToken}`
          return api(original)   // its own failure (403, 500) is its own error, not the old 401
        }, () => Promise.reject(toApiError(err)))   // the refresh failed: the session is gone
      }

      isRefreshing = true
      try {
        const res = await axios.post('/api/auth/refresh', { refresh_token: refreshToken })
        const { access_token, refresh_token } = res.data
        localStorage.setItem('token', access_token)
        localStorage.setItem('refresh_token', refresh_token)
        refreshQueue.forEach(p => p.resolve(access_token))
        refreshQueue = []
        original.headers.Authorization = `Bearer ${access_token}`
        return api(original)
      } catch {
        refreshQueue.forEach(p => p.reject())
        refreshQueue = []
        localStorage.removeItem('token')
        localStorage.removeItem('refresh_token')
        window.location.href = '/login'
        return Promise.reject(toApiError(err))   // 401: the session is gone, not a server fault
      } finally {
        isRefreshing = false
      }
    }

    return Promise.reject(toApiError(err))
  },
)

export default api
