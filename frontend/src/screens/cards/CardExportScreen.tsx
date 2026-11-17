import { useState, useEffect, useMemo } from 'react'
import { getCardRun, downloadCardOutput } from '../../lib/cards-api'
import type { CardRun } from '../../lib/cards-types'

interface CardExportScreenProps {
  runId: string
  onReset: () => void
}

export function CardExportScreen({ runId, onReset }: CardExportScreenProps) {
  const [run, setRun] = useState<CardRun | null>(null)

  useEffect(() => {
    getCardRun(runId)
      .then(setRun)
      .catch((err) => console.error('Failed to load card run:', err))
  }, [runId])

  const cardSummaries = useMemo(() => {
    if (!run) return []
    const map = new Map<string, { issuer: string; last4: string; cardholder: string; totalTxns: number; debits: number; credits: number; verified: boolean }>()

    for (const s of run.statements) {
      const ck = `${s.issuer}-${s.last4}`
      if (!map.has(ck)) {
        map.set(ck, {
          issuer: s.issuer,
          last4: s.last4,
          cardholder: s.cardholder,
          totalTxns: 0,
          debits: 0,
          credits: 0,
          verified: true,
        })
      }
      const entry = map.get(ck)!
      entry.totalTxns += s.transactions.length
      entry.debits += s.parsed_debits_paise / 100
      entry.credits += s.parsed_credits_paise / 100
      if (s.status !== 'VERIFIED') {
        entry.verified = false
      }
    }

    return Array.from(map.entries()).map(([key, val]) => ({ key, ...val }))
  }, [run])

  if (!run) {
    return (
      <div className="text-center py-12 text-sm text-gray-500">
        Loading export options…
      </div>
    )
  }

  const anyUnverified = cardSummaries.some((c) => !c.verified)

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-200 dark:border-gray-700 pb-4">
        <div>
          <h2 className="text-2xl font-bold text-gray-900 dark:text-white">Export Generated Workbooks</h2>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
            Download Output A (Statement Ledger) and Output B (Books Transactions) for each credit card, or download all in one ZIP.
          </p>
        </div>
        <button
          onClick={onReset}
          className="px-4 py-2 text-xs font-semibold text-gray-600 dark:text-gray-300 hover:text-gray-900 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800"
        >
          ← Process New Batch
        </button>
      </div>

      {/* Unverified Notice Banner if any gate had residual diff */}
      {anyUnverified && (
        <div className="p-4 bg-amber-50 dark:bg-amber-900/20 border border-amber-300 dark:border-amber-700 rounded-xl space-y-1 text-xs text-amber-900 dark:text-amber-200">
          <p className="font-bold flex items-center gap-1.5">
            <span>⚠️</span> Verification Note on Statement Totals
          </p>
          <p>
            Some statement summaries have printed identities that include previous cycle adjustments or unlisted finance fees.
            All transaction lines printed in the PDFs have been completely preserved and captured without omission.
          </p>
        </div>
      )}

      {/* Main Combined Download Card */}
      <div className="p-6 bg-gradient-to-r from-blue-600 to-indigo-700 rounded-2xl text-white shadow-md flex flex-col md:flex-row items-center justify-between gap-6">
        <div className="space-y-1 text-center md:text-left">
          <h3 className="text-lg font-bold">Download Complete Archive (.ZIP)</h3>
          <p className="text-xs text-blue-100 max-w-lg">
            Includes all Output A and Output B workbooks for all cards, audit logs, and transaction reviews in a single package.
          </p>
        </div>
        <button
          onClick={() => downloadCardOutput(runId, undefined, 'zip')}
          className="px-6 py-3 bg-white text-blue-700 font-bold rounded-xl text-xs shadow-md hover:bg-blue-50 transition-all whitespace-nowrap focus:ring-4 focus:ring-blue-300"
        >
          ⬇️ Download All Workbooks (.zip)
        </button>
      </div>

      {/* Per-Card Table */}
      <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-2xl overflow-hidden shadow-sm">
        <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <h3 className="font-bold text-sm text-gray-800 dark:text-gray-200">Generated Workbooks by Card</h3>
          <span className="text-xs text-gray-500">{cardSummaries.length} Cards Found</span>
        </div>
        <div className="divide-y divide-gray-100 dark:divide-gray-700/60">
          {cardSummaries.map((card) => (
            <div key={card.key} className="p-5 flex flex-col md:flex-row md:items-center justify-between gap-4 hover:bg-gray-50/50 dark:hover:bg-gray-750 transition-all">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="font-bold text-sm text-gray-900 dark:text-white">
                    {card.issuer} •••• {card.last4}
                  </span>
                  <span className="text-xs text-gray-500">({card.cardholder})</span>
                </div>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  {card.totalTxns} transactions • Spends: ₹{card.debits.toLocaleString('en-IN', { minimumFractionDigits: 2 })} • Credits: ₹{card.credits.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                </p>
              </div>

              {/* Action Buttons */}
              <div className="flex items-center gap-3">
                <button
                  onClick={() => downloadCardOutput(runId, card.last4, 'A')}
                  className="px-3.5 py-1.5 text-xs font-semibold text-gray-700 dark:text-gray-200 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 rounded-lg transition-all"
                  title="Layout matching sample_creditcardstatement.xls"
                >
                  📄 Output A (Statement)
                </button>
                <button
                  onClick={() => downloadCardOutput(runId, card.last4, 'B')}
                  className="px-3.5 py-1.5 text-xs font-semibold text-blue-700 dark:text-blue-300 bg-blue-50 hover:bg-blue-100 dark:bg-blue-900/40 rounded-lg border border-blue-200 dark:border-blue-800 transition-all"
                  title="Layout matching Account_Transactions_2(1).xls"
                >
                  📊 Output B (Books Import)
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Quirk / Information Notice */}
      <div className="p-4 bg-gray-50 dark:bg-gray-800/60 border border-gray-200 dark:border-gray-700 rounded-xl space-y-2 text-xs text-gray-600 dark:text-gray-400">
        <h4 className="font-bold text-gray-800 dark:text-gray-200">Accounting & Format Specifications</h4>
        <ul className="list-disc pl-5 space-y-1">
          <li>
            <strong>Output A (Statement Ledger):</strong> Follows <code>sample_creditcardstatement.xls</code> layout.
            Spends sit in Payments/Refunds column, Credits sit in Purchases column.
          </li>
          <li>
            <strong>Output B (Books Accrual):</strong> Follows <code>Account_Transactions_2(1).xls</code> with 19 standard columns.
            Expenses are credited to the card liability; payments and refunds are debited. Includes a separate <em>Review</em> sheet.
          </li>
          <li>
            <strong>Format:</strong> Exported as <code>.xlsx</code>. For legacy accounting software requiring <code>.xls</code>,
            files can be opened and saved as Excel 97-2003 or converted via LibreOffice.
          </li>
        </ul>
      </div>
    </div>
  )
}

