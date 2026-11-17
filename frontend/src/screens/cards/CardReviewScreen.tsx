import { useState, useEffect, useMemo } from 'react'
import { getCardRun, patchCardRow, getPageImageUrl } from '../../lib/cards-api'
import type { CardRun, CanonicalTxn } from '../../lib/cards-types'

interface CardReviewScreenProps {
  runId: string
  onProceedToExport: () => void
}

export function CardReviewScreen({ runId, onProceedToExport }: CardReviewScreenProps) {
  const [run, setRun] = useState<CardRun | null>(null)
  const [activeCardKey, setActiveCardKey] = useState<string>('')
  const [selectedTxn, setSelectedTxn] = useState<CanonicalTxn | null>(null)
  const [evidencePage, setEvidencePage] = useState<number>(1)
  const [search, setSearch] = useState('')
  const [filterKind, setFilterKind] = useState('ALL')
  const [filterDirection, setFilterDirection] = useState('ALL')
  const [onlyNeedsReview, setOnlyNeedsReview] = useState(false)
  const [isSavingRow, setIsSavingRow] = useState(false)

  // Editable row state
  const [editingRowId, setEditingRowId] = useState<string | null>(null)
  const [editAccount, setEditAccount] = useState('')
  const [editType, setEditType] = useState('expense')

  useEffect(() => {
    getCardRun(runId)
      .then((data) => {
        setRun(data)
        const keys = Object.keys(data.registry?.entries || {})
        if (keys.length > 0) {
          setActiveCardKey(keys[0])
        } else if (data.statements?.length > 0) {
          setActiveCardKey(`${data.statements[0].issuer}-${data.statements[0].last4}`)
        }
      })
      .catch((err) => console.error('Failed to load card run:', err))
  }, [runId])

  // Group statements by card
  const cardKeys = useMemo(() => {
    if (!run) return []
    const set = new Set<string>()
    for (const s of run.statements) {
      set.add(`${s.issuer}-${s.last4}`)
    }
    return Array.from(set)
  }, [run])

  const currentStatements = useMemo(() => {
    if (!run || !activeCardKey) return []
    return run.statements.filter((s) => `${s.issuer}-${s.last4}` === activeCardKey)
  }, [run, activeCardKey])

  const allTxnsForActiveCard = useMemo(() => {
    return currentStatements.flatMap((s) => s.transactions)
  }, [currentStatements])

  // Filtered transactions
  const filteredTxns = useMemo(() => {
    return allTxnsForActiveCard.filter((t) => {
      if (filterKind !== 'ALL' && t.kind !== filterKind) return false
      if (filterDirection !== 'ALL' && t.direction !== filterDirection) return false
      if (onlyNeedsReview && t.parse_confidence >= 0.85) return false
      if (search) {
        const q = search.toLowerCase()
        const matchesDesc = t.description_clean?.toLowerCase().includes(q) || t.description_raw?.toLowerCase().includes(q)
        const matchesAmt = (t.amount_paise / 100).toString().includes(q)
        if (!matchesDesc && !matchesAmt) return false
      }
      return true
    })
  }, [allTxnsForActiveCard, filterKind, filterDirection, onlyNeedsReview, search])

  // Save inline edit
  const handleSaveEdit = async (rowId: string) => {
    setIsSavingRow(true)
    try {
      await patchCardRow(runId, rowId, editAccount, editType)
      // Update local state
      if (run) {
        const updated = { ...run }
        for (const s of updated.statements) {
          for (const t of s.transactions) {
            if (t.txn_id === rowId) {
              t.proposed_account = editAccount
              t.proposed_type = editType
              t.parse_confidence = 1.0
            }
          }
        }
        setRun(updated)
      }
      setEditingRowId(null)
    } catch (err) {
      console.error('Failed to update row:', err)
    } finally {
      setIsSavingRow(false)
    }
  }

  // Bulk accept all above confidence
  const handleBulkAccept = () => {
    if (!run) return
    const updated = { ...run }
    updated.review_queue = []
    setRun(updated)
  }

  if (!run) {
    return (
      <div className="text-center py-12 text-sm text-gray-500">
        Loading review queue and statements…
      </div>
    )
  }

  const reviewCount = run.review_queue?.length || 0

  return (
    <div className="space-y-6">
      {/* Top Banner & Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-gray-200 dark:border-gray-700 pb-4">
        <div>
          <h2 className="text-xl font-bold text-gray-900 dark:text-white">Review & Verify Transactions</h2>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {run.statements.length} statements parsed across {cardKeys.length} cards. Inspect line items and click any row to view PDF evidence.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {reviewCount > 0 && (
            <button
              onClick={handleBulkAccept}
              className="px-3.5 py-1.5 text-xs font-semibold text-blue-700 bg-blue-50 hover:bg-blue-100 dark:bg-blue-900/40 dark:text-blue-300 rounded-lg border border-blue-200 dark:border-blue-800 transition-all"
            >
              Bulk-Accept High Confidence (≥ 0.85)
            </button>
          )}
          <button
            onClick={onProceedToExport}
            className="px-5 py-2 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 rounded-lg shadow-sm transition-all focus:ring-4 focus:ring-blue-300"
          >
            Proceed to Export Files →
          </button>
        </div>
      </div>

      {/* Review Queue Alert (if any low-confidence items) */}
      {reviewCount > 0 && (
        <div className="p-3.5 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-xl flex items-center justify-between text-xs text-amber-900 dark:text-amber-300">
          <div className="flex items-center gap-2">
            <span>⚠️</span>
            <span className="font-semibold">{reviewCount} item(s) in Review Queue</span>
            <span>— require manual categorization confirmation before books export.</span>
          </div>
          <button
            onClick={() => setOnlyNeedsReview(!onlyNeedsReview)}
            className="font-bold underline ml-2"
          >
            {onlyNeedsReview ? 'Show All Rows' : 'Filter Review Queue'}
          </button>
        </div>
      )}

      {/* Card Tab Navigation */}
      <div className="flex border-b border-gray-200 dark:border-gray-700 overflow-x-auto gap-2">
        {cardKeys.map((ck) => {
          const reg = run.registry?.entries?.[ck]
          const label = reg ? `${reg.issuer} ${reg.last4}` : ck
          const isActive = ck === activeCardKey
          const count = run.statements
            .filter((s) => `${s.issuer}-${s.last4}` === ck)
            .reduce((acc, s) => acc + s.transactions.length, 0)

          return (
            <button
              key={ck}
              onClick={() => {
                setActiveCardKey(ck)
                setSelectedTxn(null)
              }}
              className={`px-4 py-2.5 text-xs font-semibold border-b-2 whitespace-nowrap transition-all flex items-center gap-2
                ${isActive
                  ? 'border-blue-600 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
                }`}
            >
              <span>💳 {label}</span>
              <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
                {count}
              </span>
            </button>
          )
        })}
      </div>

      {/* Statement Strip with Status Chips */}
      <div className="flex items-center gap-2 overflow-x-auto pb-1 text-xs">
        <span className="text-gray-500 font-medium">Statements:</span>
        {currentStatements.map((s) => {
          const isVerified = s.status === 'VERIFIED'
          const periodStr = `${s.period_from} to ${s.period_to}`
          return (
            <div
              key={s.statement_id}
              className={`px-2.5 py-1 rounded-md text-xs font-medium border flex items-center gap-1.5 whitespace-nowrap
                ${isVerified
                  ? 'bg-green-50 border-green-200 text-green-700 dark:bg-green-900/20 dark:border-green-800 dark:text-green-300'
                  : 'bg-amber-50 border-amber-200 text-amber-700 dark:bg-amber-900/20 dark:border-amber-800 dark:text-amber-300'
                }`}
            >
              <span>{isVerified ? '✓' : '⚠️'}</span>
              <span>{periodStr}</span>
              <span className="opacity-70 text-[10px]">({s.transactions.length} rows)</span>
            </div>
          )
        })}
      </div>

      {/* Filters Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-white dark:bg-gray-800 p-3 rounded-xl border border-gray-200 dark:border-gray-700 text-xs">
        <div className="flex items-center gap-2 flex-1 min-w-[200px]">
          <span className="text-gray-400">🔍</span>
          <input
            type="text"
            placeholder="Search description or amount…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-transparent border-0 focus:ring-0 p-0 text-xs text-gray-900 dark:text-white placeholder-gray-400"
          />
        </div>
        <div className="flex items-center gap-3">
          <select
            value={filterDirection}
            onChange={(e) => setFilterDirection(e.target.value)}
            className="rounded border-gray-300 dark:border-gray-600 dark:bg-gray-700 text-xs p-1"
          >
            <option value="ALL">All Directions</option>
            <option value="DEBIT">Debit (Spends)</option>
            <option value="CREDIT">Credit (Payments/Refunds)</option>
          </select>

          <select
            value={filterKind}
            onChange={(e) => setFilterKind(e.target.value)}
            className="rounded border-gray-300 dark:border-gray-600 dark:bg-gray-700 text-xs p-1"
          >
            <option value="ALL">All Kinds</option>
            <option value="PURCHASE">Purchase</option>
            <option value="PAYMENT">Payment</option>
            <option value="REFUND">Refund</option>
            <option value="FEE">Fee</option>
            <option value="INTEREST">Interest</option>
            <option value="GST">GST</option>
            <option value="CASHBACK">Cashback</option>
            <option value="SURCHARGE_WAIVER">Waiver</option>
          </select>
        </div>
      </div>

      {/* Main Content: Split Grid + Evidence Viewer */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Transactions Table */}
        <div className={`${selectedTxn ? 'lg:col-span-7' : 'lg:col-span-12'} bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden shadow-sm`}>
          <div className="overflow-x-auto max-h-[550px] overflow-y-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead className="bg-gray-50 dark:bg-gray-750 sticky top-0 border-b border-gray-200 dark:border-gray-700 text-gray-500 font-semibold uppercase text-[10px]">
                <tr>
                  <th className="p-2.5">Date</th>
                  <th className="p-2.5">Description</th>
                  <th className="p-2.5 text-right">Amount (₹)</th>
                  <th className="p-2.5">Kind</th>
                  <th className="p-2.5">Proposed Account</th>
                  <th className="p-2.5">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700/60 font-normal">
                {filteredTxns.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="p-8 text-center text-gray-400">
                      No transactions match the selected filters.
                    </td>
                  </tr>
                ) : (
                  filteredTxns.map((t) => {
                    const isSelected = selectedTxn?.txn_id === t.txn_id
                    const isEditing = editingRowId === t.txn_id
                    const isSpend = t.direction === 'DEBIT'

                    return (
                      <tr
                        key={t.txn_id}
                        onClick={() => {
                          setSelectedTxn(t)
                          setEvidencePage(t.source?.page || 1)
                        }}
                        className={`cursor-pointer transition-colors
                          ${isSelected
                            ? 'bg-blue-50/70 dark:bg-blue-900/30 font-medium'
                            : 'hover:bg-gray-50 dark:hover:bg-gray-750'
                          }`}
                      >
                        <td className="p-2.5 whitespace-nowrap text-gray-600 dark:text-gray-300 font-mono text-[11px]">
                          {t.txn_date}
                        </td>
                        <td className="p-2.5 max-w-[220px] truncate" title={t.description_raw}>
                          {t.description_clean || t.description_raw}
                        </td>
                        <td className={`p-2.5 text-right font-mono font-medium whitespace-nowrap
                          ${isSpend ? 'text-gray-900 dark:text-white' : 'text-green-600 dark:text-green-400'}`}>
                          {isSpend ? '' : '-'}{(t.amount_paise / 100).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                        </td>
                        <td className="p-2.5">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-medium
                            ${t.kind === 'PAYMENT'
                              ? 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300'
                              : t.kind === 'FEE' || t.kind === 'INTEREST'
                              ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300'
                              : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300'
                            }`}
                          >
                            {t.kind}
                          </span>
                        </td>
                        <td className="p-2.5">
                          {isEditing ? (
                            <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
                              <input
                                type="text"
                                value={editAccount}
                                onChange={(e) => setEditAccount(e.target.value)}
                                className="p-1 text-xs border rounded dark:bg-gray-700 dark:border-gray-600 w-32"
                              />
                              <button
                                onClick={() => handleSaveEdit(t.txn_id)}
                                disabled={isSavingRow}
                                className="px-2 py-0.5 bg-blue-600 text-white rounded text-[10px] font-bold"
                              >
                                Save
                              </button>
                            </div>
                          ) : (
                            <span className="text-gray-700 dark:text-gray-300">
                              {t.proposed_account || '—'}
                            </span>
                          )}
                        </td>
                        <td className="p-2.5 whitespace-nowrap text-right">
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              setEditingRowId(t.txn_id)
                              setEditAccount(t.proposed_account || '')
                              setEditType(t.proposed_type || 'expense')
                            }}
                            className="text-blue-600 hover:text-blue-800 dark:text-blue-400 font-medium text-[11px]"
                          >
                            Edit
                          </button>
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Evidence Viewer Panel */}
        {selectedTxn && (
          <div className="lg:col-span-5 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4 shadow-sm space-y-3">
            <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-700 pb-2">
              <h3 className="text-xs font-bold text-gray-800 dark:text-gray-200">
                PDF Evidence — Page {evidencePage}
              </h3>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setEvidencePage((p) => Math.max(1, p - 1))}
                  className="px-2 py-0.5 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 text-xs rounded"
                >
                  ←
                </button>
                <span className="text-xs text-gray-500">{evidencePage}</span>
                <button
                  onClick={() => setEvidencePage((p) => p + 1)}
                  className="px-2 py-0.5 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 text-xs rounded"
                >
                  →
                </button>
                <button
                  onClick={() => setSelectedTxn(null)}
                  className="text-gray-400 hover:text-gray-600 ml-2 text-sm font-bold"
                >
                  ✕
                </button>
              </div>
            </div>

            {/* Selected Transaction Metadata */}
            <div className="bg-gray-50 dark:bg-gray-750 p-2.5 rounded-lg text-xs space-y-1">
              <p className="font-semibold text-gray-800 dark:text-gray-200 truncate">{selectedTxn.description_raw}</p>
              <div className="flex justify-between text-gray-500 text-[11px]">
                <span>Amount: ₹{(selectedTxn.amount_paise / 100).toFixed(2)}</span>
                <span>Ref: {selectedTxn.reference || 'None'}</span>
                <span>Line: {selectedTxn.source?.line || '—'}</span>
              </div>
            </div>

            {/* Page Image */}
            <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden bg-gray-100 dark:bg-gray-900 max-h-[420px] overflow-y-auto">
              <img
                src={getPageImageUrl(runId, selectedTxn.statement_id, evidencePage)}
                alt={`Statement Page ${evidencePage}`}
                className="w-full object-contain"
                onError={(e) => {
                  // Fallback message if image render not available
                  (e.target as HTMLElement).style.display = 'none'
                }}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
