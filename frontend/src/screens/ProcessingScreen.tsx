import { useState, useEffect } from 'react'
import { subscribeToEvents } from '../lib/api'

type StepStatus = 'pending' | 'active' | 'done' | 'error'

interface Step {
  id: string
  label: string
  detail: string
  status: StepStatus
}

const STEPS: { id: string; label: string }[] = [
  { id: 'DETECT', label: 'Detect file types' },
  { id: 'PARSE', label: 'Parse files' },
  { id: 'L1_EXACT', label: 'L1: Exact matching' },
  { id: 'L2_REF_GROUP', label: 'L2: Reference groups' },
  { id: 'L3_AGGREGATE', label: 'L3: Aggregate matching' },
  { id: 'L4_FUZZY', label: 'L4: Fuzzy matching' },
  { id: 'L5_RESIDUAL', label: 'L5: Classify residuals' },
  { id: 'GATES', label: 'Accuracy gates' },
  { id: 'AI_REVIEW', label: 'AI review' },
  { id: 'BUILD_WORKBOOK', label: 'Build workbook' },
]

interface ProcessingScreenProps {
  runId: string
  onDone: () => void
}

export function ProcessingScreen({ runId, onDone }: ProcessingScreenProps) {
  const [steps, setSteps] = useState<Step[]>(
    STEPS.map((s) => ({ ...s, detail: '', status: 'pending' as StepStatus }))
  )
  const [messages, setMessages] = useState<string[]>([])
  const [llmStatus, setLlmStatus] = useState<string>('')
  const [error, setError] = useState('')

  useEffect(() => {
    const unsub = subscribeToEvents(
      runId,
      (event, data) => {
        if (event === 'progress') {
          const stepId = (data.step as string) || ''
          const detail = (data.detail as string) || ''
          setSteps((prev) => {
            const idx = prev.findIndex((s) => s.id === stepId)
            if (idx === -1) return prev
            return prev.map((s, i) => {
              if (i < idx) return { ...s, status: 'done' }
              if (i === idx) return { ...s, status: 'active', detail }
              return s
            })
          })
          if (stepId === 'AI_FALLBACK') {
            setLlmStatus('AI unavailable — rule-based suggestions')
          }
        } else if (event === 'info' || event === 'warning') {
          setMessages((prev) => [...prev, (data.message as string) || ''])
        } else if (event === 'error') {
          setError((data.message as string) || 'An error occurred')
          setSteps((prev) =>
            prev.map((s) => (s.status === 'active' ? { ...s, status: 'error' } : s))
          )
        } else if (event === 'detect') {
          const bank = data.bank as Record<string, unknown>
          const books = data.books as Record<string, unknown>
          if (bank && books) {
            const msg = `Detected: ${bank.note} · ${books.note}`
            setMessages((prev) => [...prev, msg])
          }
        }
      },
      () => {
        setSteps((prev) => prev.map((s) => (s.status !== 'error' ? { ...s, status: 'done' } : s)))
        setTimeout(onDone, 500)
      },
      (err) => setError(err)
    )
    return unsub
  }, [runId, onDone])

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="text-center space-y-2">
        <h2 className="text-2xl font-bold text-gray-900 dark:text-white">Reconciling…</h2>
        <p className="text-gray-500 dark:text-gray-400 text-sm">Run ID: {runId.slice(0, 8)}…</p>
      </div>

      {/* LLM status */}
      {llmStatus && (
        <div className="text-sm bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700 text-amber-700 dark:text-amber-300 px-3 py-2 rounded-lg">
          ⚠ {llmStatus}
        </div>
      )}

      {/* Stepper */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
        {steps.map((step, i) => (
          <div
            key={step.id}
            className={`flex items-start gap-4 px-5 py-4 ${
              i < steps.length - 1 ? 'border-b border-gray-100 dark:border-gray-700' : ''
            }`}
          >
            <div className="mt-0.5 flex-shrink-0">
              {step.status === 'done' ? (
                <div className="w-6 h-6 rounded-full bg-green-500 flex items-center justify-center">
                  <span className="text-white text-xs">✓</span>
                </div>
              ) : step.status === 'active' ? (
                <div className="w-6 h-6 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
              ) : step.status === 'error' ? (
                <div className="w-6 h-6 rounded-full bg-red-500 flex items-center justify-center">
                  <span className="text-white text-xs">✗</span>
                </div>
              ) : (
                <div className="w-6 h-6 rounded-full border-2 border-gray-300 dark:border-gray-600" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className={`text-sm font-medium ${
                step.status === 'active' ? 'text-blue-600 dark:text-blue-400'
                : step.status === 'done' ? 'text-green-600 dark:text-green-400'
                : step.status === 'error' ? 'text-red-600 dark:text-red-400'
                : 'text-gray-400 dark:text-gray-500'
              }`}>
                {step.label}
              </div>
              {step.detail && (
                <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 truncate">
                  {step.detail}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Info messages */}
      {messages.length > 0 && (
        <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-100 dark:border-blue-800 rounded-lg p-3 space-y-1">
          {messages.map((msg, i) => (
            <p key={i} className="text-xs text-blue-700 dark:text-blue-300">{msg}</p>
          ))}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 px-4 py-3 rounded-lg">
          <strong>Error:</strong> {error}
          <p className="text-xs mt-1 text-red-500">Fix hint: {error}</p>
        </div>
      )}
    </div>
  )
}

