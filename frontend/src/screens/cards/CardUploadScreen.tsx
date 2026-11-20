import React, { useState, useRef, useCallback } from 'react'
import { uploadCardFiles } from '../../lib/cards-api'

interface CardUploadScreenProps {
  onRunCreated: (runId: string) => void
}

export function CardUploadScreen({ onRunCreated }: CardUploadScreenProps) {
  const [zipFile, setZipFile] = useState<File | null>(null)
  const [booksFile, setBooksFile] = useState<File | null>(null)
  const [orgName, setOrgName] = useState('Xiarch Bharat Private Limited')
  const [locationName, setLocationName] = useState('XIARCH GURGAON BRANCH')
  const [isUploading, setIsUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragOverZip, setDragOverZip] = useState(false)
  const [dragOverBooks, setDragOverBooks] = useState(false)

  const zipInputRef = useRef<HTMLInputElement>(null)
  const [pdfFiles, setPdfFiles] = useState<File[]>([])
  const [dragOverPdf, setDragOverPdf] = useState(false)
  const pdfInputRef = useRef<HTMLInputElement>(null)
  const booksInputRef = useRef<HTMLInputElement>(null)

  const handleZipDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOverZip(false)
    const file = e.dataTransfer.files[0]
    if (file && (file.name.endsWith('.zip') || file.type.includes('zip'))) {
      setZipFile(file)
      setError(null)
    } else {
      setError('Please upload a valid .zip file containing statement PDFs')
    }
  }, [])

  const handleBooksDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOverBooks(false)
    const file = e.dataTransfer.files[0]
    if (file && (file.name.endsWith('.xls') || file.name.endsWith('.xlsx'))) {
      setBooksFile(file)
    }
  }, [])

  const addPdfs = (list: FileList | File[]) => {
    const incoming = Array.from(list).filter((f) => f.name.toLowerCase().endsWith('.pdf'))
    if (incoming.length === 0) {
      setError('Please select PDF files only')
      return
    }
    setPdfFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`))
      return [...prev, ...incoming.filter((f) => !seen.has(`${f.name}:${f.size}`))]
    })
    setError(null)
  }

  const handlePdfDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOverPdf(false)
    addPdfs(e.dataTransfer.files)
  }, [])

  const removePdf = (idx: number) =>
    setPdfFiles((prev) => prev.filter((_, i) => i !== idx))

  const hasInput = !!zipFile || pdfFiles.length > 0

  const handleStartProcessing = async () => {
    if (!hasInput) {
      setError('Please add a ZIP file and/or at least one PDF statement')
      return
    }
    setIsUploading(true)
    setError(null)
    try {
      const resp = await uploadCardFiles(zipFile, pdfFiles, booksFile || undefined, orgName, locationName)
      onRunCreated(resp.run_id)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Upload failed')
      setIsUploading(false)
    }
  }

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      {/* Title & Introduction */}
      <div className="text-center space-y-2">
        <h2 className="text-2xl font-bold tracking-tight text-gray-900 dark:text-white">
          Credit Card PDFs → Statement & Books Excel
        </h2>
        <p className="text-sm text-gray-600 dark:text-gray-400 max-w-2xl mx-auto">
          Upload a ZIP archive and/or multiple PDFs of monthly credit card statement PDFs (HDFC, Axis, ICICI, IDFC, SBI).
          The engine reads every PDF, verifies totals to the paisa, and fills both Output A (Statement) and Output B (Books) workbooks.
        </p>
      </div>

      {error && (
        <div className="p-4 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-lg flex items-center justify-between">
          <p className="text-sm text-red-800 dark:text-red-300 font-medium">{error}</p>
          <button onClick={() => setError(null)} className="text-xs text-red-600 hover:underline">Dismiss</button>
        </div>
      )}

      {/* Main ZIP Drop Zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOverZip(true) }}
        onDragLeave={() => setDragOverZip(false)}
        onDrop={handleZipDrop}
        onClick={() => zipInputRef.current?.click()}
        className={`border-2 border-dashed rounded-2xl p-10 text-center cursor-pointer transition-all duration-200
          ${dragOverZip
            ? 'border-blue-500 bg-blue-50/50 dark:bg-blue-900/20'
            : zipFile
            ? 'border-green-500 bg-green-50/30 dark:bg-green-900/10'
            : 'border-gray-300 dark:border-gray-700 hover:border-blue-400 bg-white dark:bg-gray-800/50'
          }`}
      >
        <input
          ref={zipInputRef}
          type="file"
          accept=".zip"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) {
              setZipFile(f)
              setError(null)
            }
          }}
        />
        <div className="flex flex-col items-center justify-center space-y-3">
          <div className="w-14 h-14 rounded-full bg-blue-100 dark:bg-blue-900/50 flex items-center justify-center text-blue-600 dark:text-blue-300 text-2xl font-bold">
            📦
          </div>
          <div>
            <p className="text-base font-semibold text-gray-800 dark:text-gray-200">
              {zipFile ? zipFile.name : 'Drop statement ZIP archive here, or click to browse'}
            </p>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
              {zipFile
                ? `${(zipFile.size / (1024 * 1024)).toFixed(2)} MB — Ready to process`
                : 'Supports folders with multiple issuers (HDFC, Axis, ICICI, IDFC, SBI)'}
            </p>
          </div>
          {zipFile && (
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300">
              ✓ Archive attached
            </span>
          )}
        </div>
      </div>

      {/* AND / OR: Multiple PDF upload */}
      <div className="flex items-center gap-3 text-xs text-gray-500">
        <div className="flex-1 border-t border-gray-300 dark:border-gray-700" />
        <span>AND / OR</span>
        <div className="flex-1 border-t border-gray-300 dark:border-gray-700" />
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragOverPdf(true) }}
        onDragLeave={() => setDragOverPdf(false)}
        onDrop={handlePdfDrop}
        onClick={() => pdfInputRef.current?.click()}
        className={`border-2 border-dashed rounded-2xl p-6 text-center cursor-pointer transition-all duration-200
          ${dragOverPdf
            ? 'border-blue-500 bg-blue-50/50 dark:bg-blue-900/20'
            : pdfFiles.length > 0
            ? 'border-green-500 bg-green-50/30 dark:bg-green-900/10'
            : 'border-gray-300 dark:border-gray-700 hover:border-blue-400 bg-white dark:bg-gray-800/50'
          }`}
      >
        <input
          ref={pdfInputRef}
          type="file"
          accept=".pdf,application/pdf"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) addPdfs(e.target.files)
            e.target.value = ''
          }}
        />
        <div className="flex flex-col items-center space-y-2">
          <span className="text-3xl">📄</span>
          <p className="text-base font-semibold text-gray-800 dark:text-gray-200">
            Drop multiple credit card PDFs here, or click to browse
          </p>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {pdfFiles.length > 0
              ? `${pdfFiles.length} PDF${pdfFiles.length > 1 ? 's' : ''} selected — click to add more`
              : 'Select several statement PDFs at once (HDFC, Axis, ICICI, IDFC, SBI)'}
          </p>
        </div>
      </div>

      {pdfFiles.length > 0 && (
        <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl p-3 max-h-48 overflow-y-auto">
          <div className="flex justify-between items-center mb-2">
            <p className="text-xs font-semibold text-gray-700 dark:text-gray-300">
              {pdfFiles.length} PDF file{pdfFiles.length > 1 ? 's' : ''}
            </p>
            <button onClick={() => setPdfFiles([])} className="text-xs text-red-600 hover:underline">
              Clear all
            </button>
          </div>
          <ul className="space-y-1">
            {pdfFiles.map((f, i) => (
              <li key={`${f.name}-${i}`} className="flex items-center justify-between text-xs text-gray-700 dark:text-gray-300">
                <span className="truncate">{f.name}</span>
                <span className="flex items-center gap-3 shrink-0">
                  <span className="text-gray-400">{(f.size / 1024).toFixed(0)} KB</span>
                  <button onClick={() => removePdf(i)} className="text-red-500 hover:text-red-700">✕</button>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Secondary Options */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Optional Reference Books Export */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOverBooks(true) }}
          onDragLeave={() => setDragOverBooks(false)}
          onDrop={handleBooksDrop}
          onClick={() => booksInputRef.current?.click()}
          className={`border border-dashed rounded-xl p-5 text-center cursor-pointer transition-all
            ${dragOverBooks ? 'border-purple-500 bg-purple-50/50' : 'border-gray-300 dark:border-gray-700 hover:border-purple-400 bg-white dark:bg-gray-800'}
          `}
        >
          <input
            ref={booksInputRef}
            type="file"
            accept=".xls,.xlsx"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) setBooksFile(f)
            }}
          />
          <div className="flex items-center gap-3">
            <span className="text-2xl">📊</span>
            <div className="text-left flex-1">
              <p className="text-xs font-semibold text-gray-700 dark:text-gray-300">
                {booksFile ? booksFile.name : 'Optional: Reference Books Export (.xls)'}
              </p>
              <p className="text-[11px] text-gray-500 dark:text-gray-400">
                {booksFile ? 'Will learn account names from history' : 'Used to learn merchant-to-account mappings'}
              </p>
            </div>
            {booksFile && (
              <span className="text-xs text-purple-600 font-bold">✓ Attached</span>
            )}
          </div>
        </div>

        {/* Organisation & Settings */}
        <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl p-5 space-y-3">
          <div>
            <label className="block text-xs font-medium text-gray-700 dark:text-gray-300">Organisation Name</label>
            <input
              type="text"
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              className="mt-1 block w-full rounded-md border-gray-300 dark:border-gray-600 dark:bg-gray-700 shadow-sm text-xs p-1.5 focus:ring-blue-500 focus:border-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 dark:text-gray-300">Location Name</label>
            <input
              type="text"
              value={locationName}
              onChange={(e) => setLocationName(e.target.value)}
              className="mt-1 block w-full rounded-md border-gray-300 dark:border-gray-600 dark:bg-gray-700 shadow-sm text-xs p-1.5 focus:ring-blue-500 focus:border-blue-500"
            />
          </div>
        </div>
      </div>

      {/* Info Notice about Column Convention */}
      <div className="p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-xl text-xs text-blue-900 dark:text-blue-300 space-y-1">
        <p className="font-semibold flex items-center gap-1.5">
          <span>ℹ️</span> Output Column Convention Note
        </p>
        <p>
          Following the user-supplied <code>sample_creditcardstatement.xls</code> layout, debit spends are placed in the{' '}
          <strong>Payments/Refunds</strong> column and credit payments are placed in the <strong>Purchases</strong> column.
          This matches the sample file format exactly.
        </p>
      </div>

      {/* Primary Action Button */}
      <div className="flex justify-end pt-2">
        <button
          onClick={handleStartProcessing}
          disabled={!hasInput || isUploading}
          className={`px-6 py-2.5 rounded-lg text-sm font-semibold text-white transition-all shadow-sm
            ${!hasInput || isUploading
              ? 'bg-gray-400 cursor-not-allowed'
              : 'bg-blue-600 hover:bg-blue-700 focus:ring-4 focus:ring-blue-300'
            }`}
        >
          {isUploading ? 'Uploading & Starting…' : 'Process Statements →'}
        </button>
      </div>
    </div>
  )
}

