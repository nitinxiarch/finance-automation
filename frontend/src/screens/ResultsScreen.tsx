import { useState, useEffect } from 'react'
import { getRun, downloadWorkbook } from '../lib/api'
import {
  formatPaise, statusColor, statusLabel, codeColor,
  type RunResponse, type ExceptionRecord, type GateResult,
} from '../lib/types'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from 'recharts'

interface ResultsScreenProps {
  runId: string
  onReset: () => void
}

function GatesBadge({ gates }: { gates: GateResult[] }) {
  const passed = gates.filter((g) => g.passed).length
  const total = gates.length
  const allOk = passed === total
  return (
    <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium border ${
      allOk ? 'bg-green-50 border-green-200 text-green-700' : 'bg-red-50 border-red-200 text-red-700'
    }`}>
      {allOk ? '✓' : '✗'} {passed}/{total} gates passed
    </div>
  )
}

function ExceptionDrawer({
  exc,
  onClose,
}: {
  exc: ExceptionRecord
  onClose: () => void
}) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative w-full max-w-lg bg-white dark:bg-gray-800 shadow-2xl overflow-y-auto p-6 space-y-4">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="font-bold text-lg">{exc.exception_id}</h3>
            <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${codeColor(exc.code)}`}>
              {exc.code}
            </span>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded">✕</button>
        </div>

        <div className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <div className="text-gray-500 text-xs">Amount</div>
            <div className="font-semibold tabular-nums">{formatPaise(exc.amount_paise)}</div>
          </div>
          <div>
            <div className="text-gray-500 text-xs">Owner</div>
            <div>{exc.owner_team || '—'}</div>
          </div>
          <div className="col-span-2">
            <div className="text-gray-500 text-xs">Evidence</div>
            <div className="text-xs text-gray-700 dark:text-gray-300">{exc.evidence}</div>
          </div>
          {exc.bank_row_ids.length > 0 && (
            <div>
              <div className="text-gray-500 text-xs">Bank rows</div>
              <div className="text-xs font-mono">{exc.bank_row_ids.join(', ')}</div>
            </div>
          )}
          {exc.books_row_ids.length > 0 && (
            <div>
              <div className="text-gray-500 text-xs">Books rows</div>
              <div className="text-xs font-mono">{exc.books_row_ids.join(', ')}</div>
            </div>
          )}
        </div>

        {exc.ai_card && (
          <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h4 className="font-semibold text-sm">AI Action Card</h4>
              <div className="flex items-center gap-2">
                <span className={`text-xs px-2 py-0.5 rounded-full ${
                  exc.ai_card.priority === 'P1' ? 'bg-red-100 text-red-700'
                  : exc.ai_card.priority === 'P2' ? 'bg-amber-100 text-amber-700'
                  : 'bg-gray-100 text-gray-600'
                }`}>
                  {exc.ai_card.priority}
                </span>
                {exc.ai_verified && (
                  <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full">✓ Verified</span>
                )}
              </div>
            </div>
            <p className="text-sm text-gray-700 dark:text-gray-300">{exc.ai_card.summary}</p>
            {exc.ai_card.recommended_actions.length > 0 && (
              <div>
                <div className="text-xs text-gray-500 mb-1">Actions:</div>
                <ol className="space-y-1">
                  {exc.ai_card.recommended_actions.map((a, i) => (
                    <li key={i} className="text-xs flex gap-2">
                      <span className="text-gray-400">{i + 1}.</span>
                      <span>{a.step} <em className="text-gray-400">({a.who}, {a.due_in_days}d)</em></span>
                    </li>
                  ))}
                </ol>
              </div>
            )}
            {exc.ai_card.customer_message_draft && (
              <div>
                <div className="text-xs text-gray-500 mb-1">Customer message draft:</div>
                <div className="bg-gray-50 dark:bg-gray-700 p-2 rounded text-xs text-gray-700 dark:text-gray-300 italic">
                  {exc.ai_card.customer_message_draft}
                </div>
              </div>
            )}
          </div>
        )}

        {exc.needs_human && (
          <div className="bg-amber-50 border border-amber-200 text-amber-700 px-3 py-2 rounded-lg text-xs">
            ⚠ This exception needs human confirmation.
          </div>
        )}
      </div>
    </div>
  )
}

export function ResultsScreen({ runId, onReset }: ResultsScreenProps) {
  const [run, setRun] = useState<RunResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedExc, setSelectedExc] = useState<ExceptionRecord | null>(null)
  const [filterCode, setFilterCode] = useState('')
  const [filterOwner, setFilterOwner] = useState('')

  useEffect(() => {
    getRun(runId).then(setRun).finally(() => setLoading(false))
  }, [runId])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full" />
      </div>
    )
  }

  if (!run?.result) {
    return (
      <div className="text-center py-16 text-gray-500">
        <p className="text-lg">No result found for this run.</p>
        <p className="text-sm mt-2">{run?.error}</p>
        <button onClick={onReset} className="mt-4 text-blue-600 hover:underline">← Try again</button>
      </div>
    )
  }

  const result = run.result
  const { exceptions, matches, gate_results } = result

  // Waterfall chart data
  const bridgeData = [
    { name: 'Books Closing', value: result.books_closing_paise / 100, fill: '#6366f1' },
    ...exceptions.map((e) => ({
      name: `${e.exception_id} (${e.code})`,
      value: e.amount_paise / 100,
      fill: '#f59e0b',
    })),
    { name: 'Bank Closing', value: result.bank_closing_paise / 100, fill: '#22c55e' },
  ]

  const filteredExceptions = exceptions.filter((e) => {
    if (filterCode && e.code !== filterCode) return false
    if (filterOwner && e.owner_team !== filterOwner) return false
    return true
  })

  const uniqueCodes = [...new Set(exceptions.map((e) => e.code))]
  const uniqueOwners = [...new Set(exceptions.map((e) => e.owner_team).filter(Boolean))]
  const unexplainedSum = exceptions
    .filter((e) => e.code === 'UNEXPLAINED')
    .reduce((s, e) => s + e.amount_paise, 0)

  return (
    <div className="space-y-6">
      {/* Status banner */}
      <div className={`px-6 py-4 rounded-xl border-2 ${statusColor(result.status)}`}>
        <div className="flex items-center justify-between">
          <div>
            <div className="text-xl font-bold">{statusLabel(result.status)}</div>
            {!result.all_gates_passed && (
              <div className="text-sm mt-1">
                Failed: {gate_results.filter((g) => !g.passed).map((g) => g.gate).join(', ')}
              </div>
            )}
          </div>
          <GatesBadge gates={gate_results} />
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          { label: 'Bank Closing', value: result.bank_closing_paise, color: 'blue' },
          { label: 'Books Closing', value: result.books_closing_paise, color: 'purple' },
          { label: 'Difference', value: Math.abs(result.bank_closing_paise - result.books_closing_paise), color: 'amber' },
          { label: 'Unexplained', value: unexplainedSum, color: unexplainedSum === 0 ? 'green' : 'red' },
        ].map((card) => (
          <div key={card.label} className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl p-4">
            <div className="text-xs text-gray-500 dark:text-gray-400">{card.label}</div>
            <div className={`text-lg font-bold tabular-nums ${
              card.color === 'green' ? 'text-green-600' :
              card.color === 'red' ? 'text-red-600' :
              card.color === 'blue' ? 'text-blue-600' :
              card.color === 'purple' ? 'text-purple-600' :
              'text-amber-600'
            }`}>
              {formatPaise(card.value)}
            </div>
            {card.label === 'Unexplained' && unexplainedSum === 0 && (
              <div className="text-xs text-green-600 mt-0.5">✓ Zero unexplained</div>
            )}
          </div>
        ))}
      </div>

      {/* Waterfall bridge */}
      <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl p-4">
        <h3 className="font-semibold text-sm mb-3 text-gray-700 dark:text-gray-200">
          Reconciliation Bridge: Books → Bank Closing
        </h3>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={bridgeData} margin={{ top: 10, right: 20, left: 10, bottom: 40 }}>
            <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-30} textAnchor="end" />
            <YAxis tick={{ fontSize: 10 }} tickFormatter={(v: number) => `₹${(v/100000).toFixed(1)}L`} />
            <Tooltip formatter={(v: number) => formatPaise(v * 100)} />
            <Bar dataKey="value">
              {bridgeData.map((entry, i) => (
                <Cell key={i} fill={entry.fill} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Exceptions table */}
      <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between flex-wrap gap-3">
          <h3 className="font-semibold text-gray-800 dark:text-gray-100">
            Exceptions ({filteredExceptions.length}/{exceptions.length})
          </h3>
          <div className="flex gap-2">
            <select
              value={filterCode}
              onChange={(e) => setFilterCode(e.target.value)}
              className="text-sm border border-gray-200 dark:border-gray-600 rounded-lg px-2 py-1 bg-white dark:bg-gray-700"
            >
              <option value="">All codes</option>
              {uniqueCodes.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <select
              value={filterOwner}
              onChange={(e) => setFilterOwner(e.target.value)}
              className="text-sm border border-gray-200 dark:border-gray-600 rounded-lg px-2 py-1 bg-white dark:bg-gray-700"
            >
              <option value="">All owners</option>
              {uniqueOwners.map((o) => (
                <option key={o} value={o!}>{o}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-700 text-left">
                <th className="px-4 py-2 font-medium text-gray-600 dark:text-gray-300">ID</th>
                <th className="px-4 py-2 font-medium text-gray-600 dark:text-gray-300">Code</th>
                <th className="px-4 py-2 font-medium text-gray-600 dark:text-gray-300">Amount</th>
                <th className="px-4 py-2 font-medium text-gray-600 dark:text-gray-300">Owner</th>
                <th className="px-4 py-2 font-medium text-gray-600 dark:text-gray-300">AI</th>
              </tr>
            </thead>
            <tbody>
              {filteredExceptions.map((exc, i) => (
                <tr
                  key={exc.exception_id}
                  className={`border-t border-gray-100 dark:border-gray-700 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-750 ${
                    i % 2 === 0 ? '' : 'bg-gray-50/50 dark:bg-gray-800/50'
                  }`}
                  onClick={() => setSelectedExc(exc)}
                >
                  <td className="px-4 py-2 font-mono text-xs text-gray-600 dark:text-gray-400">{exc.exception_id}</td>
                  <td className="px-4 py-2">
                    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${codeColor(exc.code)}`}>
                      {exc.code}
                    </span>
                  </td>
                  <td className="px-4 py-2 tabular-nums font-semibold">{formatPaise(exc.amount_paise)}</td>
                  <td className="px-4 py-2 text-xs text-gray-600 dark:text-gray-400">{exc.owner_team || '—'}</td>
                  <td className="px-4 py-2">
                    {exc.ai_card ? (
                      exc.ai_verified
                        ? <span className="text-xs text-green-600">✓ Verified</span>
                        : <span className="text-xs text-gray-400">Draft</span>
                    ) : <span className="text-xs text-gray-300">—</span>}
                  </td>
                </tr>
              ))}
              {filteredExceptions.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-gray-400">No exceptions match the filter</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Match summary */}
      <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl p-5">
        <h3 className="font-semibold text-gray-800 dark:text-gray-100 mb-3">Match Summary</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
          {(['L1_EXACT', 'L2_REFERENCE_GROUP', 'L3_AGGREGATE', 'L4_FUZZY'] as const).map((layer) => {
            const count = matches.filter((m) => m.layer === layer).length
            return (
              <div key={layer} className="text-center">
                <div className="text-2xl font-bold text-blue-600">{count}</div>
                <div className="text-xs text-gray-500">{layer.replace('_', ' ')}</div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Sticky download bar */}
      <div className="sticky bottom-0 bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 shadow-lg p-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between gap-4 flex-wrap">
          <div className="text-sm text-gray-600 dark:text-gray-300">
            Run: <span className="font-mono">{runId.slice(0, 8)}…</span>
            {' · '}{matches.length} matches · {exceptions.length} exceptions
          </div>
          <div className="flex gap-3">
            <button
              onClick={() => downloadWorkbook(runId)}
              className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            >
              ⬇ Download Workbook (.xlsx)
            </button>
            <button
              onClick={() => {
                const csv = [
                  'ID,Code,Amount (₹),Owner,Evidence',
                  ...exceptions.map((e) =>
                    `${e.exception_id},${e.code},${(e.amount_paise/100).toFixed(2)},${e.owner_team || ''},${JSON.stringify(e.evidence)}`
                  ),
                ].join('\n')
                const blob = new Blob([csv], { type: 'text/csv' })
                const url = URL.createObjectURL(blob)
                const a = document.createElement('a')
                a.href = url
                a.download = `exceptions_${runId.slice(0, 8)}.csv`
                a.click()
              }}
              className="border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            >
              ⬇ Exceptions CSV
            </button>
          </div>
        </div>
      </div>

      {/* Drawer */}
      {selectedExc && (
        <ExceptionDrawer exc={selectedExc} onClose={() => setSelectedExc(null)} />
      )}
    </div>
  )
}

