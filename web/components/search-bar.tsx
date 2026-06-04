"use client"

import { useEffect, useState } from "react"
import { Search } from "lucide-react"

interface SearchBarProps {
  onSearch: (query: string) => void
  isLoading: boolean
  currentQuery?: string
}

export default function SearchBar({ onSearch, isLoading, currentQuery = "" }: SearchBarProps) {
  const [value, setValue] = useState(currentQuery)

  useEffect(() => {
    setValue(currentQuery)
  }, [currentQuery])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (value.trim()) onSearch(value.trim())
  }

  return (
    <form onSubmit={handleSubmit} className="flex items-center gap-2">
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 dark:text-gray-500" />
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="dental clinics in Houston..."
          className="pl-9 pr-4 py-2 w-72 rounded-lg bg-white/80 border border-gray-200 dark:bg-white/5 dark:border-white/10 dark:text-white dark:placeholder:text-gray-500 text-sm
                     placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-teal-500/40"
        />
      </div>
      <button
        type="submit"
        disabled={isLoading || !value.trim()}
        className="px-4 py-2 rounded-lg bg-teal-600 text-white text-sm font-medium
                   hover:bg-teal-700 disabled:opacity-50 transition"
      >
        {isLoading ? "Scanning..." : "Scan City"}
      </button>
    </form>
  )
}
