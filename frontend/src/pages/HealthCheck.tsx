import React, { useEffect, useState } from 'react'

interface HealthData {
  status: string
  sim_time: number
  version: string
}

/**
 * Placeholder page for M0.
 * Calls GET /api/health and renders the response.
 * Will be replaced by Mission Control in M6.
 */
export default function HealthCheck() {
  const [data, setData] = useState<HealthData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/health')
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<HealthData>
      })
      .then((json) => {
        setData(json)
        setLoading(false)
      })
      .catch((err: Error) => {
        setError(err.message)
        setLoading(false)
      })
  }, [])

  return (
    <div className="max-w-xl">
      <h1 className="text-2xl font-bold mb-4 text-blue-400">TrustHeal Backend</h1>
      <p className="text-gray-400 mb-6 text-sm">
        This is the M0 placeholder page. The full dashboard will be built in M6.
      </p>

      <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
          GET /api/health
        </h2>

        {loading && (
          <p className="text-gray-500 text-sm animate-pulse">Connecting to backend…</p>
        )}

        {error && (
          <div className="text-red-400 text-sm">
            <span className="font-semibold">Error:</span> {error}
            <p className="text-gray-500 mt-1">
              Make sure the backend is running: <code className="text-gray-300">make dev</code>
            </p>
          </div>
        )}

        {data && (
          <div className="space-y-2">
            <Row label="Status">
              <span className={data.status === 'ok' ? 'text-green-400 font-semibold' : 'text-red-400'}>
                {data.status}
              </span>
            </Row>
            <Row label="Version">
              <span className="text-gray-300 font-mono">{data.version}</span>
            </Row>
            <Row label="Sim time">
              <span className="text-gray-300 font-mono">{data.sim_time}s</span>
            </Row>
          </div>
        )}
      </div>

      {data && (
        <p className="mt-4 text-xs text-gray-600">
          Backend connected ✓ — OpenAPI schema at{' '}
          <a href="/docs" className="text-blue-500 hover:underline" target="_blank" rel="noreferrer">
            /docs
          </a>
        </p>
      )}
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <span className="text-gray-500 text-xs w-20 shrink-0">{label}</span>
      <span>{children}</span>
    </div>
  )
}
