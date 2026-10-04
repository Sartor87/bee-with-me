import { describe, it, expect, afterEach } from 'vitest'
import api, { ApiError, errorText, detailOf } from './client'

const defaultAdapter = api.defaults.adapter

function failWith(status, data) {
  api.defaults.adapter = async (config) => {
    const response = { status, data, headers: {}, config }
    const err = new Error(`Request failed with status code ${status}`)
    err.config = config
    err.response = response
    throw err
  }
}

describe('api client rejections', () => {
  afterEach(() => { api.defaults.adapter = defaultAdapter; localStorage.clear() })

  it('rejects with an Error that carries the detail and the status [B39]', async () => {
    failWith(409, { detail: 'settings_stale' })
    const err = await api.get('/settings').catch(e => e)
    expect(err).toBeInstanceOf(Error)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.message).toBe('settings_stale')
    expect(err.detail).toBe('settings_stale')
    expect(err.status).toBe(409)
    expect(detailOf(err)).toBe('settings_stale')
    expect(errorText(err)).toBe('settings_stale')
  })

  it('keeps a validation array as detail and gives readable text [B39]', async () => {
    failWith(422, { detail: [{ msg: 'too big' }, { msg: 'too small' }] })
    const err = await api.put('/settings/hq', {}).catch(e => e)
    expect(Array.isArray(err.detail)).toBe(true)
    expect(err.status).toBe(422)
    expect(err.message).toBe('too big; too small')
  })

  it('has no status when no response arrived [B39]', async () => {
    api.defaults.adapter = async () => { throw Object.assign(new Error('Network Error'), { config: {} }) }
    const err = await api.get('/x').catch(e => e)
    expect(err.status).toBeUndefined()
    expect(err.message).toBe('Network Error')
  })

  it('a 401 with no refresh token still rejects with status 401 [B39]', async () => {
    failWith(401, { detail: 'Not authenticated' })
    const err = await api.get('/x').catch(e => e)
    expect(err.status).toBe(401)
    expect(err.detail).toBe('Not authenticated')
  })

  it('errorText and detailOf accept bare strings and fall back [B39]', () => {
    expect(errorText('boom')).toBe('boom')
    expect(errorText(undefined, 'fallback')).toBe('fallback')
    expect(detailOf('settings_stale')).toBe('settings_stale')
  })
})
