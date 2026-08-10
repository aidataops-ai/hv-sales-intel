"use client"

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react"

import ConfigButton from "@/components/config-button"
import ExportButton from "@/components/export-button"
import Pagination from "@/components/pagination"
import RetriggerButton from "@/components/retrigger-button"
import SignalsFilterBar from "@/components/signals-filter-bar"
import SignalsTable from "@/components/signals-table"
import SignalsTopBar from "@/components/signals-top-bar"
import { ApproveModal, RejectModal } from "@/components/signal-action-modals"
import {
  filterParams, getFilterOptions, listLeads, updateLead,
  type FilterOptions, type Lead,
} from "@/lib/leads"
import { useSignalsUrlState, EMPTY_SIGNALS_STATE } from "@/lib/use-signals-url-state"

const PAGE_SIZE = 25

export default function SignalsPage() {
  return (
    <Suspense fallback={<div className="h-screen w-screen" />}>
      <SignalsContent />
    </Suspense>
  )
}

function SignalsContent() {
  const [state, setState] = useSignalsUrlState()

  const [leads, setLeads] = useState<Lead[]>([])
  const [total, setTotal] = useState(0)
  const [options, setOptions] = useState<FilterOptions>({
    cities: [], tracks: [], states: [],
  })
  const [isLoading, setIsLoading] = useState(true)
  const [busyIds, setBusyIds] = useState<Set<number>>(new Set())
  const [approving, setApproving] = useState<Lead | null>(null)
  const [rejecting, setRejecting] = useState<Lead | null>(null)

  // Monotonic request id: any filter change bumps it so a slow in-flight
  // response resolves into a no-op instead of overwriting a newer page.
  const reqIdRef = useRef(0)
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const activeFilters = useMemo(
    () => filterParams(state),
    // The filter fields, not the whole state — re-sorting shouldn't rebuild
    // the export params object and re-render the button.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [state.cities, state.tracks, state.band, state.decision,
     state.work_mode, state.source, state.salary,
     state.search],
  )

  const load = useCallback(async () => {
    const reqId = ++reqIdRef.current
    setIsLoading(true)
    try {
      const page = await listLeads({
        filters: state,
        sort: state.sort,
        dir: state.dir,
        offset: (state.page - 1) * PAGE_SIZE,
        limit: PAGE_SIZE,
      })
      if (reqId !== reqIdRef.current) return
      setLeads(page.leads)
      setTotal(page.total)
    } finally {
      if (reqId === reqIdRef.current) setIsLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(state)])

  useEffect(() => {
    // Debounced so typing in the search box doesn't fire a request per key.
    const timer = setTimeout(load, 250)
    return () => clearTimeout(timer)
  }, [load])

  useEffect(() => {
    getFilterOptions().then(setOptions)
  }, [])

  const patch = useCallback(
    async (lead: Lead, fields: Parameters<typeof updateLead>[1]) => {
      setBusyIds((prev) => new Set(prev).add(lead.id))
      try {
        const updated = await updateLead(lead.id, fields)
        setLeads((prev) => prev.map((l) => (l.id === lead.id ? updated : l)))
        return updated
      } finally {
        setBusyIds((prev) => {
          const next = new Set(prev)
          next.delete(lead.id)
          return next
        })
      }
    },
    [],
  )

  const toggleSort = useCallback(
    (key: string) => {
      setState(
        state.sort === key
          ? { dir: state.dir === "asc" ? "desc" : "asc" }
          : { sort: key, dir: key === "posted" ? "desc" : "asc" },
      )
    },
    [state.sort, state.dir, setState],
  )

  return (
    <div className="min-h-screen bg-cream dark:bg-night-900">
      <SignalsTopBar>
        <ConfigButton />
        <RetriggerButton />
        <ExportButton
          endpoint="/api/leads/export.csv"
          label="Export the signals matching your current filters"
          params={activeFilters}
        />
      </SignalsTopBar>

      <main className="pt-14">
        <div className="max-w-[1600px] mx-auto p-4">
          <div className="glass-panel dark:bg-night-800/90 dark:border-white/10 rounded-2xl overflow-hidden">
            <div className="px-5 pt-5 pb-1 flex items-start justify-between gap-4">
              <div>
                <h1 className="font-serif text-lg font-semibold text-gray-900 dark:text-white">
                  Instant Signals
                </h1>
                <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
                  {isLoading && leads.length === 0
                    ? "Loading…"
                    : `${total} signal${total !== 1 ? "s" : ""}`}
                  <span className="text-gray-400 dark:text-gray-500">
                    {" · practices hiring for the roles we place"}
                  </span>
                </p>
              </div>
            </div>

            <SignalsFilterBar
              state={state}
              onChange={setState}
              options={options}
              onReset={() => setState(EMPTY_SIGNALS_STATE)}
            />

            <SignalsTable
              leads={leads}
              isLoading={isLoading}
              state={state}
              onSort={toggleSort}
              onApprove={setApproving}
              onReject={setRejecting}
              busyIds={busyIds}
            />

            <div className="border-t border-gray-200/50 dark:border-white/10">
              <Pagination
                page={state.page}
                totalPages={totalPages}
                onChange={(page) => setState({ page })}
              />
            </div>
          </div>
        </div>
      </main>

      {approving && (
        <ApproveModal
          lead={approving}
          isSaving={busyIds.has(approving.id)}
          onClose={() => setApproving(null)}
          onConfirm={async () => {
            await patch(approving, { disposition: "approved" })
            setApproving(null)
          }}
        />
      )}

      {rejecting && (
        <RejectModal
          lead={rejecting}
          isSaving={busyIds.has(rejecting.id)}
          onClose={() => setRejecting(null)}
          onConfirm={async (reason) => {
            await patch(rejecting, { disposition: "rejected", reject_reason: reason })
            setRejecting(null)
          }}
        />
      )}
    </div>
  )
}
