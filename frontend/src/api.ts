import type { CompareResponse, Config, PolicyInfo, Protocol, SimulateResponse, SweepResponse, Template, TraceResponse } from './types'
import { SimulateError, type ApiError } from './types'

function normalizeDetail(body: any): ApiError {
  const detail = body?.detail
  // Our sandbox/algorithm errors: {type, title, message, line}.
  if (detail && typeof detail === 'object' && !Array.isArray(detail) && 'message' in detail) {
    return detail as ApiError
  }
  // FastAPI/pydantic 422 body shape: {detail: [{loc, msg, type}, ...]}.
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0]
    const field = Array.isArray(first.loc) ? first.loc.slice(1).join('.') : ''
    return {
      type: 'validation_error',
      title: 'Invalid Request',
      message: field ? `${field}: ${first.msg}` : first.msg,
    }
  }
  return {
    type: 'bad_request',
    title: 'Request Failed',
    message: typeof detail === 'string' ? detail : JSON.stringify(body),
  }
}

async function asJson<T>(res: Response): Promise<T> {
  const body = await res.json()
  if (!res.ok) {
    throw new SimulateError(normalizeDetail(body))
  }
  return body as T
}

export async function fetchPolicies(): Promise<PolicyInfo[]> {
  const res = await fetch('/api/policies')
  const body = await asJson<{ builtin: PolicyInfo[] }>(res)
  return body.builtin
}

export async function fetchTemplates(): Promise<Template[]> {
  const res = await fetch('/api/templates')
  const body = await asJson<{ templates: Template[] }>(res)
  return body.templates
}

export async function fetchConfigDefaults(protocol: Protocol): Promise<Config> {
  const res = await fetch(`/api/config/defaults?protocol=${protocol}`)
  return asJson<Config>(res)
}

interface SimulateArgs {
  protocol: Protocol
  policy?: string
  algorithm?: string
  config?: Partial<Config>
}

export async function simulate(args: SimulateArgs): Promise<SimulateResponse> {
  const res = await fetch('/api/simulate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  })
  return asJson<SimulateResponse>(res)
}

interface CompareArgs {
  protocol: Protocol
  policies: string[]
  algorithm?: string
  config?: Partial<Config>
}

export async function compare(args: CompareArgs): Promise<CompareResponse> {
  const res = await fetch('/api/simulate/compare', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  })
  return asJson<CompareResponse>(res)
}

interface SweepArgs {
  protocol: Protocol
  policies: string[]
  algorithm?: string
  contentions: number[]
  config?: Partial<Config>
}

export async function sweep(args: SweepArgs): Promise<SweepResponse> {
  const res = await fetch('/api/simulate/sweep', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  })
  return asJson<SweepResponse>(res)
}

interface TraceArgs {
  protocol: Protocol
  policies: string[]
  algorithm?: string
  config?: Partial<Config>
  window_start?: number
  window_size?: number
  core_id?: number
}

export async function trace(args: TraceArgs): Promise<TraceResponse> {
  const res = await fetch('/api/simulate/trace', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  })
  return asJson<TraceResponse>(res)
}
