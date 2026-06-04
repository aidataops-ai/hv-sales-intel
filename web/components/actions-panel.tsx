"use client"

import { useState, ReactNode } from "react"
import { cn } from "@/lib/utils"

interface Tab {
  id: string
  label: string
  disabled?: boolean
  badge?: number
}

interface ActionsPanelProps {
  tabs: Tab[]
  renderTab: (id: string) => ReactNode
  defaultTab?: string
}

export default function ActionsPanel({ tabs, renderTab, defaultTab }: ActionsPanelProps) {
  const [active, setActive] = useState(defaultTab ?? tabs[0].id)

  return (
    <div className="flex flex-col h-full">
      <div className="flex border-b border-gray-200/60 dark:border-white/10">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => !tab.disabled && setActive(tab.id)}
            disabled={tab.disabled}
            className={cn(
              "flex-1 text-sm font-medium py-2 border-b-2 transition",
              active === tab.id
                ? "border-teal-600 text-teal-700 dark:text-teal-400"
                : "border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-[#d9d9d9]",
              tab.disabled && "opacity-40 cursor-not-allowed"
            )}
          >
            {tab.label}
            {tab.badge && tab.badge > 0 ? (
              <span className="ml-1.5 inline-flex items-center justify-center min-w-[16px] h-4 px-1 text-[10px] font-bold text-white bg-rose-500 rounded-full">
                {tab.badge}
              </span>
            ) : null}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto mt-4">{renderTab(active)}</div>
    </div>
  )
}
