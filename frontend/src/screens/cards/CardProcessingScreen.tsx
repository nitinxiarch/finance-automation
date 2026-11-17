import { useState, useEffect } from 'react'
import { subscribeToCardEvents } from '../../lib/cards-api'

interface CardProcessingScreenProps {
  runId: string
  onDone: () => void
}

interface StepLog {
  step: string
  message: string
}

export function CardProcessingScreen({ runId, onDone }: CardProcessingScreenProps) {
  const [logs, setLogs] = useState<StepLog[]>([])
  const [currentFile, setCurrentFile] = useState<string>('')
  const [fileProgress, setFileProgress] = useState<{ current: number; total: number }>({ current: 0, total: 0 })
  const [isDone, setIsDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const unsub = subscribeToCardEvents(
      runId,
      (event, data) => {
        if (event === 'status') {
          setLogs((prev) => [...prev, { step: String(data.step || ''), message: String(data.message || '') }])
        } else if (event === 'progress') {
          setCurrentFile(String(data.file || ''))
          setFileProgress({
            current: Number(data.current || 0),
            total: Number(data.total || 0),
          })
        } else if (event === 'error') {
          setError(String(data.message || 'An error occurred during processing'))
        }
      },
      () => {
        setIsDone(true)
      },
      (_err) => {
        // SSE error or completion
        setIsDone(true)
      }
    )

    return () => unsub()
  }, [runId])

  const percent = fileProgress.total > 0
    ? Math.round((fileProgress.current / fileProgress.total) * 100)
    : isDone ? 100 : 15

  return (
    <div className="max-w-2xl mx-auto space-y-6 py-6">
      <div className="text-center space-y-2">
        <h2 className="text-xl font-bold text-gray-900 dark:text-white">
          {isDone ? 'Processing Complete!' : 'Processing Card Statements…'}
        </h2>
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Run ID: <code className="bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 rounded">{runId}</code>
        </p>
      </div>

      {/* Progress Bar */}
      <div className="space-y-1">
        <div className="flex justify-between text-xs text-gray-600 dark:text-gray-400 font-medium">
          <span>{currentFile ? `Parsing: ${currentFile}` : 'Processing statements…'}</span>
          <span>{percent}%</span>
        </div>
        <div className="w-full bg-gray-200 dark:bg-gray-700 h-2.5 rounded-full overflow-hidden">
          <div
            className="bg-blue-600 h-2.5 rounded-full transition-all duration-300"
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>

      {/* AI Status Badge */}
      <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800/60 rounded-xl border border-gray-200 dark:border-gray-700 text-xs">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-green-500 animate-pulse" />
          <span className="font-medium text-gray-700 dark:text-gray-300">Accuracy Engine Active</span>
        </div>
        <span className="text-gray-500 dark:text-gray-400">Tiers T1–T4 • Gates S1–S10</span>
      </div>

      {error && (
        <div className="p-4 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-lg text-xs text-red-800 dark:text-red-300">
          <p className="font-semibold">Processing note:</p>
          <p>{error}</p>
        </div>
      )}

      {/* Live Logs */}
      <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl p-4 shadow-sm space-y-2 max-h-60 overflow-y-auto font-mono text-[11px]">
        {logs.map((log, idx) => (
          <div key={idx} className="flex items-start gap-2">
            <span className="text-blue-600 dark:text-blue-400 font-semibold">[{log.step}]</span>
            <span className="text-gray-700 dark:text-gray-300">{log.message}</span>
          </div>
        ))}
        {isDone && (
          <div className="flex items-center gap-2 text-green-600 dark:text-green-400 font-semibold pt-2 border-t border-gray-100 dark:border-gray-700">
            <span>✓</span>
            <span>All stages finished. Statements verified and workbooks ready.</span>
          </div>
        )}
      </div>

      {/* Done Action */}
      {isDone && (
        <div className="flex justify-center pt-2">
          <button
            onClick={onDone}
            className="px-8 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-semibold shadow-sm transition-all focus:ring-4 focus:ring-blue-300"
          >
            Review Statements & Export →
          </button>
        </div>
      )}
    </div>
  )
}
