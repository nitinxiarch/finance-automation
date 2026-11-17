import { useState, useCallback, useRef } from 'react'
import { uploadFiles, checkHealth } from '../lib/api'
import type { HealthResponse } from '../lib/types'

interface DetectedFile {
  file: File | null
  type: 'BANK_STATEMENT' | 'BOOKS_EXPORT' | 'UNKNOWN' | null
  note: string
  period?: string
  rows?: number
  size: number
}

function FileDrop({
  label,
  accept,
  detected,
  onFile,
}: {
  label: string
  accept: string
  detected: DetectedFile
  onFile: (f: File) => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragging(false)
      const f = e.dataTransfer.files[0]
      if (f) onFile(f)
    },
    [onFile]
  )

  const typeColor =
    detected.type === 'BANK_STATEMENT'
      ? 'bg-blue-100 text-blue-700'
      : detected.type === 'BOOKS_EXPORT'
      ? 'bg-green-100 text-green-700'
      : detected.type === 'UNKNOWN'
      ? 'bg-red-100 text-red-700'
      : 'bg-gray-100 text-gray-500'

  return (
    <div
      className={`relative border-2 border-dashed rounded-xl p-8 text-center transition-all cursor-pointer
        ${dragging ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-blue-400 hover:bg-gray-50'}
        dark:border-gray-600 dark:hover:border-blue-500 dark:hover:bg-gray-800`}
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      onClick={() => ref.current?.click()}
    >
      <input
        ref={ref}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => { if (e.target.files?.[0]) onFile(e.target.files[0]) }}
      />

      {detected.file ? (
        <div className="space-y-2">
          <div className="text-3xl">📄</div>
          <div className="font-semibold text-gray-800 dark:text-gray-100 truncate">
            {detected.file.name}
          </div>
          {detected.type && (
            <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${typeColor}`}>
              {detected.type === 'BANK_STATEMENT' ? '🏦 Bank Statement' : detected.type === 'BOOKS_EXPORT' ? '📚 Books Export' : '❓ Unknown'}
            </span>
          )}
          {detected.note && (
            <p className="text-xs text-gray-500 dark:text-gray-400">{detected.note}</p>
          )}
          {detected.period && (
            <p className="text-xs text-gray-600 dark:text-gray-300">📅 {detected.period}</p>
          )}
          {detected.rows !== undefined && (
            <p className="text-xs text-gray-500">{detected.rows} rows · {(detected.size / 1024).toFixed(1)} KB</p>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          <div className="text-4xl opacity-30">📁</div>
          <p className="font-medium text-gray-600 dark:text-gray-300">{label}</p>
          <p className="text-sm text-gray-400">Drag & drop or click to browse</p>
          <p className="text-xs text-gray-400">{accept}</p>
        </div>
      )}
    </div>
  )
}

interface UploadScreenProps {
  onRunCreated: (runId: string) => void
}

export function UploadScreen({ onRunCreated }: UploadScreenProps) {
  const [fileA, setFileA] = useState<DetectedFile>({ file: null, type: null, note: '', size: 0 })
  const [fileB, setFileB] = useState<DetectedFile>({ file: null, type: null, note: '', size: 0 })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [swapNote, setSwapNote] = useState('')
  const [booksOpening, setBooksOpening] = useState('')
  const [maskNames, setMaskNames] = useState(false)

  // Health check on mount
  useState(() => {
    checkHealth()
      .then(setHealth)
      .catch(() => setHealth(null))
  })

  const handleFile = useCallback((which: 'a' | 'b', f: File) => {
    const det: DetectedFile = { file: f, type: null, note: 'Detecting…', size: f.size }
    if (which === 'a') setFileA(det)
    else setFileB(det)

    // Client-side type detection by extension (server does authoritative detection)
    const name = f.name.toLowerCase()
    if (name.endsWith('.xlsx')) {
      const updated = { ...det, type: 'BANK_STATEMENT' as const, note: 'Likely ICICI bank statement (.xlsx)' }
      if (which === 'a') setFileA(updated)
      else setFileB(updated)
    } else if (name.endsWith('.xls')) {
      const updated = { ...det, type: 'BOOKS_EXPORT' as const, note: 'Likely Zoho Books export (.xls)' }
      if (which === 'a') setFileA(updated)
      else setFileB(updated)
    } else {
      const updated = { ...det, type: 'UNKNOWN' as const, note: 'Unknown file type — server will detect' }
      if (which === 'a') setFileA(updated)
      else setFileB(updated)
    }
  }, [])

  const canSubmit = fileA.file && fileB.file && !loading

  const handleSubmit = async () => {
    if (!fileA.file || !fileB.file) return
    setLoading(true)
    setError('')
    setSwapNote('')

    try {
      const opts: Parameters<typeof uploadFiles>[2] = { maskNames }
      if (booksOpening) {
        const rupees = parseFloat(booksOpening.replace(/,/g, ''))
        if (!isNaN(rupees)) opts.booksOpeningPaise = Math.round(rupees * 100)
      }
      const resp = await uploadFiles(fileA.file, fileB.file, opts)
      onRunCreated(resp.run_id)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Upload failed')
      setLoading(false)
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="text-center space-y-2">
        <h2 className="text-2xl font-bold text-gray-900 dark:text-white">
          Upload Reconciliation Files
        </h2>
        <p className="text-gray-500 dark:text-gray-400">
          Upload your ICICI bank statement (.xlsx) and Zoho Books export (.xls).
          Files are auto-detected and swapped if needed.
        </p>
      </div>

      {/* LLM status */}
      {health && (
        <div className={`text-sm px-4 py-2 rounded-lg border flex items-center gap-2 ${
          health.llm_tunnel_ok && health.llm_model_found
            ? 'bg-green-50 border-green-200 text-green-700'
            : 'bg-amber-50 border-amber-200 text-amber-700'
        }`}>
          <span>{health.llm_tunnel_ok && health.llm_model_found ? '✓' : '⚠'}</span>
          <span>
            AI: {health.llm_tunnel_ok
              ? (health.llm_model_found ? `Model ready (${health.llm_model_tag})` : 'Tunnel connected, model not found')
              : 'Tunnel offline — rule-based fallback will be used'}
          </span>
        </div>
      )}

      {/* Drop zones */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <FileDrop
          label="Bank Statement"
          accept=".xlsx"
          detected={fileA}
          onFile={(f) => handleFile('a', f)}
        />
        <FileDrop
          label="Books Export"
          accept=".xls"
          detected={fileB}
          onFile={(f) => handleFile('b', f)}
        />
      </div>

      {swapNote && (
        <p className="text-sm text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 px-3 py-2 rounded-lg">
          ℹ {swapNote}
        </p>
      )}

      {/* Advanced options */}
      <details className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
        <summary className="cursor-pointer text-sm font-medium text-gray-600 dark:text-gray-300">
          Advanced Options
        </summary>
        <div className="mt-4 space-y-3">
          <div>
            <label className="block text-sm text-gray-600 dark:text-gray-300 mb-1">
              Books Opening Balance (₹) — optional override
            </label>
            <input
              type="text"
              placeholder="e.g. 16,57,076.02"
              value={booksOpening}
              onChange={(e) => setBooksOpening(e.target.value)}
              className="w-full border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm
                bg-white dark:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <p className="text-xs text-gray-400 mt-1">
              Default: bank opening balance (₹16,45,903.04). The app will show which was used.
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300 cursor-pointer">
            <input
              type="checkbox"
              checked={maskNames}
              onChange={(e) => setMaskNames(e.target.checked)}
              className="rounded"
            />
            Mask party names before sending to AI (data privacy)
          </label>
        </div>
      </details>

      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400
          px-4 py-3 rounded-lg text-sm">
          <strong>Error:</strong> {error}
        </div>
      )}

      <button
        onClick={handleSubmit}
        disabled={!canSubmit}
        className="w-full py-3 px-6 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed
          text-white font-semibold rounded-xl transition-colors shadow-sm text-lg"
      >
        {loading ? 'Uploading…' : 'Reconcile'}
      </button>
    </div>
  )
}

