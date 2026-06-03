"use client"

import { useEffect, useRef, useState } from "react"
import { ChevronDown, Check } from "lucide-react"
import { ALL_TAGS, TAG_LABELS, type Tag } from "@/lib/tags"
import { cn } from "@/lib/utils"

interface Props {
  selected: string[]
  onChange: (next: string[]) => void
}

export default function TagsFilter({ selected, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const wrapperRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function handleClick(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [open])

  const toggle = (tag: Tag) => {
    onChange(
      selected.includes(tag)
        ? selected.filter((t) => t !== tag)
        : [...selected, tag],
    )
  }

  const label =
    selected.length === 0
      ? "All tags"
      : selected.length === 1
        ? TAG_LABELS[selected[0] as Tag]
        : `${selected.length} tags`

  return (
    <div className="relative w-full" ref={wrapperRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full text-sm rounded-lg border border-gray-200 bg-white/80 px-3 py-2 inline-flex items-center justify-between gap-1.5 text-gray-700"
      >
        <span className="truncate">{label}</span>
        <ChevronDown className="w-3.5 h-3.5 shrink-0 text-gray-400" />
      </button>
      {open && (
        <div className="absolute z-30 mt-1 w-full min-w-[11rem] bg-white rounded-lg border border-gray-200 shadow-md">
          {ALL_TAGS.map((tag) => {
            const isSelected = selected.includes(tag)
            return (
              <button
                key={tag}
                type="button"
                onClick={() => toggle(tag)}
                className={cn(
                  "w-full text-left text-sm px-3 py-1.5 flex items-center justify-between hover:bg-gray-50",
                  isSelected && "bg-teal-50 text-teal-700",
                )}
              >
                {TAG_LABELS[tag]}
                {isSelected && <Check className="w-3.5 h-3.5" />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
