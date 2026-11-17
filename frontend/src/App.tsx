import { useState, useCallback } from 'react'
import { UploadScreen } from './screens/UploadScreen'
import { ProcessingScreen } from './screens/ProcessingScreen'
import { ResultsScreen } from './screens/ResultsScreen'
import { CardUploadScreen } from './screens/cards/CardUploadScreen'
import { CardProcessingScreen } from './screens/cards/CardProcessingScreen'
import { CardReviewScreen } from './screens/cards/CardReviewScreen'
import { CardExportScreen } from './screens/cards/CardExportScreen'

type NavTab = 'bank-recon' | 'card-statements'
type BankScreen = 'upload' | 'processing' | 'results'
type CardScreen = 'upload' | 'processing' | 'review' | 'export'

export default function App() {
  const [activeTab, setActiveTab] = useState<NavTab>('card-statements')
  const [isDark, setIsDark] = useState(false)

  // Bank-Books state
  const [bankScreen, setBankScreen] = useState<BankScreen>('upload')
  const [bankRunId, setBankRunId] = useState<string>('')

  // Card Statements state
  const [cardScreen, setCardScreen] = useState<CardScreen>('upload')
  const [cardRunId, setCardRunId] = useState<string>('')

  // Bank Handlers
  const handleBankRunCreated = useCallback((id: string) => {
    setBankRunId(id)
    setBankScreen('processing')
  }, [])

  const handleBankProcessingDone = useCallback(() => {
    setBankScreen('results')
  }, [])

  const handleBankReset = useCallback(() => {
    setBankRunId('')
    setBankScreen('upload')
  }, [])

  // Card Handlers
  const handleCardRunCreated = useCallback((id: string) => {
    setCardRunId(id)
    setCardScreen('processing')
  }, [])

  const handleCardProcessingDone = useCallback(() => {
    setCardScreen('review')
  }, [])

  const handleCardProceedToExport = useCallback(() => {
    setCardScreen('export')
  }, [])

  const handleCardReset = useCallback(() => {
    setCardRunId('')
    setCardScreen('upload')
  }, [])

  return (
    <div className={isDark ? 'dark' : ''}>
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100">
        {/* Header */}
        <header className="sticky top-0 z-20 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 shadow-sm">
          <div className="max-w-7xl mx-auto px-4 h-16 flex items-center justify-between">
            <div className="flex items-center gap-6">
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 bg-gradient-to-tr from-blue-700 to-indigo-600 rounded-lg flex items-center justify-center shadow-sm">
                  <span className="text-white text-base font-bold">₹</span>
                </div>
                <div>
                  <h1 className="font-bold text-gray-900 dark:text-white leading-tight">
                    Finance Automation
                  </h1>
                  <p className="text-xs text-gray-500 dark:text-gray-400">Zero Unexplained Discrepancy Platform</p>
                </div>
              </div>

              {/* Navigation Tabs */}
              <nav className="hidden md:flex items-center bg-gray-100 dark:bg-gray-700/60 p-1 rounded-lg">
                <button
                  type="button"
                  onClick={() => setActiveTab('card-statements')}
                  className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors flex items-center gap-2 ${
                    activeTab === 'card-statements'
                      ? 'bg-white dark:bg-gray-800 text-blue-600 dark:text-blue-400 shadow-sm'
                      : 'text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white'
                  }`}
                >
                  <span>💳</span>
                  Card Statements
                  <span className="text-[10px] bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300 px-1.5 py-0.2 rounded font-medium">New</span>
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab('bank-recon')}
                  className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors flex items-center gap-2 ${
                    activeTab === 'bank-recon'
                      ? 'bg-white dark:bg-gray-800 text-blue-600 dark:text-blue-400 shadow-sm'
                      : 'text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white'
                  }`}
                >
                  <span>🏛️</span>
                  Bank–Books Reconciliation
                </button>
              </nav>
            </div>

            {/* Right Header Actions */}
            <div className="flex items-center gap-3">
              {activeTab === 'card-statements' && cardRunId && cardScreen !== 'upload' && (
                <button
                  type="button"
                  onClick={handleCardReset}
                  className="text-xs font-medium text-blue-600 hover:text-blue-800 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/30 px-3 py-1.5 rounded-md transition-colors"
                >
                  ← New Card Run
                </button>
              )}
              {activeTab === 'bank-recon' && bankRunId && bankScreen !== 'upload' && (
                <button
                  type="button"
                  onClick={handleBankReset}
                  className="text-xs font-medium text-blue-600 hover:text-blue-800 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/30 px-3 py-1.5 rounded-md transition-colors"
                >
                  ← New Bank Recon
                </button>
              )}
              <button
                type="button"
                onClick={() => setIsDark(!isDark)}
                className="p-2 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 text-sm border border-gray-200 dark:border-gray-700"
                title="Toggle dark mode"
              >
                {isDark ? '☀️' : '🌙'}
              </button>
            </div>
          </div>

          {/* Mobile Tab Nav */}
          <div className="md:hidden flex border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/80 px-2 py-1 gap-1">
            <button
              type="button"
              onClick={() => setActiveTab('card-statements')}
              className={`flex-1 py-1.5 text-xs font-medium rounded text-center ${
                activeTab === 'card-statements'
                  ? 'bg-blue-600 text-white font-semibold'
                  : 'text-gray-600 dark:text-gray-300'
              }`}
            >
              💳 Card Statements
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('bank-recon')}
              className={`flex-1 py-1.5 text-xs font-medium rounded text-center ${
                activeTab === 'bank-recon'
                  ? 'bg-blue-600 text-white font-semibold'
                  : 'text-gray-600 dark:text-gray-300'
              }`}
            >
              🏛️ Bank Recon
            </button>
          </div>
        </header>

        {/* Main content */}
        <main className="max-w-7xl mx-auto px-4 py-8">
          {activeTab === 'card-statements' ? (
            <>
              {cardScreen === 'upload' && (
                <CardUploadScreen onRunCreated={handleCardRunCreated} />
              )}
              {cardScreen === 'processing' && cardRunId && (
                <CardProcessingScreen
                  runId={cardRunId}
                  onDone={handleCardProcessingDone}
                />
              )}
              {cardScreen === 'review' && cardRunId && (
                <CardReviewScreen
                  runId={cardRunId}
                  onProceedToExport={handleCardProceedToExport}
                />
              )}
              {cardScreen === 'export' && cardRunId && (
                <CardExportScreen
                  runId={cardRunId}
                  onReset={handleCardReset}
                />
              )}
            </>
          ) : (
            <>
              {bankScreen === 'upload' && (
                <UploadScreen onRunCreated={handleBankRunCreated} />
              )}
              {bankScreen === 'processing' && bankRunId && (
                <ProcessingScreen
                  runId={bankRunId}
                  onDone={handleBankProcessingDone}
                />
              )}
              {bankScreen === 'results' && bankRunId && (
                <ResultsScreen
                  runId={bankRunId}
                  onReset={handleBankReset}
                />
              )}
            </>
          )}
        </main>
      </div>
    </div>
  )
}
