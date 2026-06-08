"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { MapContainer, TileLayer, Marker, Popup, useMap } from "react-leaflet"
import MarkerClusterGroup from "react-leaflet-cluster"
import L from "leaflet"
import "leaflet/dist/leaflet.css"
import { Plus, Minus, Maximize2, PenLine, Layers, Crosshair, Brain, Loader2 } from "lucide-react"
import type { Practice } from "@/lib/types"
import { parseIcpBreakdown } from "@/lib/types"

// Marker popup: name + address, then either the ICP scores (if analyzed) or an
// Analyze button.
function PopupBody({
  practice,
  onAnalyze,
  analyzing,
}: {
  practice: Practice
  onAnalyze?: (placeId: string) => void
  analyzing: boolean
}) {
  const scored = practice.lead_score != null
  const breakdown = parseIcpBreakdown(practice.icp_breakdown ?? null)
    .filter((r) => r.max > 0)
    .sort((a, b) => b.score / b.max - a.score / a.max)
  return (
    <div className="min-w-[210px] space-y-2">
      <div>
        <Link
          href={`/practice/${practice.place_id}`}
          className="font-serif font-semibold !text-teal-700 dark:!text-teal-400 hover:underline block leading-tight !mb-0"
        >
          {practice.name}
        </Link>
        <p className="!my-0.5 text-xs !text-gray-500 dark:!text-gray-400">
          {practice.address}
        </p>
      </div>
      {scored ? (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <span className="px-1.5 py-0.5 rounded-full bg-teal-600 text-white text-[11px] font-bold">
              ICP {practice.lead_score}
            </span>
            {practice.rating != null && (
              <span className="px-1.5 py-0.5 rounded-full bg-gray-200 dark:bg-white/10 !text-gray-700 dark:!text-[#d9d9d9] text-[11px] font-semibold">
                ★ {practice.rating}
              </span>
            )}
          </div>
          {breakdown.length > 0 && (
            <ul className="!m-0 !p-0 !list-none space-y-0.5">
              {breakdown.map((r, i) => (
                <li
                  key={i}
                  className="flex justify-between gap-3 text-[11px] !m-0"
                >
                  <span className="!text-gray-600 dark:!text-[#d9d9d9] truncate">
                    {r.label}
                  </span>
                  <span className="!text-gray-500 dark:!text-gray-400 tabular-nums shrink-0">
                    {r.score}/{r.max}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <Link
            href={`/practice/${practice.place_id}`}
            className="inline-block text-xs font-medium !text-teal-700 dark:!text-teal-400 hover:underline"
          >
            Open Call Prep →
          </Link>
        </div>
      ) : (
        <button
          onClick={() => onAnalyze?.(practice.place_id)}
          disabled={analyzing}
          className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-teal-600 !text-white hover:bg-teal-700 disabled:opacity-50 transition"
        >
          {analyzing ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <Brain className="w-3 h-3" />
          )}
          {analyzing ? "Analyzing…" : "Analyze"}
        </button>
      )}
    </div>
  )
}

// Lead-score → marker colour. Mirrors the legend buckets exactly.
function scoreColor(score: number | null): string {
  if (score == null) return "#c2c2c2" // unscored — light gray
  if (score >= 80) return "#3c6e71" // teal (brand)
  if (score >= 60) return "#284b63" // navy
  if (score >= 40) return "#9b9b9b" // mid gray
  return "#d9d9d9" // light gray — 0–39
}

function createPinIcon(leadScore: number | null): L.DivIcon {
  const fill = scoreColor(leadScore)
  return L.divIcon({
    className: "",
    iconSize: [28, 38],
    iconAnchor: [14, 38],
    popupAnchor: [0, -36],
    html: `
      <svg width="28" height="38" viewBox="0 0 28 38" xmlns="http://www.w3.org/2000/svg">
        <path d="M14 0C6.27 0 0 6.27 0 14c0 10.5 14 24 14 24s14-13.5 14-24C28 6.27 21.73 0 14 0z"
              fill="${fill}" stroke="#fff" stroke-width="2"/>
        <circle cx="14" cy="14" r="4.5" fill="#fff" fill-opacity="0.9"/>
      </svg>`,
  })
}

// Teal circle + count, sized by child count, with a soft translucent ring.
function createClusterIcon(cluster: { getChildCount: () => number }): L.DivIcon {
  const count = cluster.getChildCount()
  const size = count < 10 ? 38 : count < 50 ? 46 : 54
  return L.divIcon({
    className: "av-cluster-wrap",
    iconSize: L.point(size, size, true),
    html: `<div class="av-cluster" style="width:${size}px;height:${size}px">${count}</div>`,
  })
}

const TILES = {
  street: {
    url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
  },
  light: {
    url: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
  },
}

function FitBounds({ points }: { points: Practice[] }) {
  const map = useMap()
  useEffect(() => {
    const pts = points
      .filter((p) => p.lat != null && p.lng != null)
      .map((p) => [p.lat!, p.lng!] as [number, number])
    if (pts.length > 0) {
      map.fitBounds(pts, { padding: [50, 50], maxZoom: 13 })
    }
  }, [points, map])
  return null
}

interface MapViewProps {
  practices: Practice[]
  selectedId: string | null
  onSelect: (placeId: string) => void
  /** Re-run the current query for what's on screen (the "Search this area" pill). */
  onSearchArea?: () => void
  /** Analyze a lead from its map popup. */
  onAnalyze?: (placeId: string) => void
  /** Place ids currently being analyzed (drives the popup spinner). */
  analyzingIds?: Set<string>
}

const LEGEND = [
  { color: "#3c6e71", label: "80 – 100" },
  { color: "#284b63", label: "60 – 79" },
  { color: "#9b9b9b", label: "40 – 59" },
  { color: "#d9d9d9", label: "0 – 39" },
]

export default function MapView({
  practices,
  selectedId,
  onSelect,
  onSearchArea,
  onAnalyze,
  analyzingIds,
}: MapViewProps) {
  const [map, setMap] = useState<L.Map | null>(null)
  const [layer, setLayer] = useState<"street" | "light">("street")
  const [isDark, setIsDark] = useState(false)
  const markerRefs = useRef<Record<string, L.Marker>>({})

  const points = practices.filter((p) => p.lat != null && p.lng != null)

  useEffect(() => {
    if (selectedId && markerRefs.current[selectedId]) {
      markerRefs.current[selectedId].openPopup()
    }
  }, [selectedId])

  // Follow the app theme: switch to a dark basemap when dark mode is active.
  useEffect(() => {
    const el = document.documentElement
    const update = () => setIsDark(el.classList.contains("dark"))
    update()
    const obs = new MutationObserver(update)
    obs.observe(el, { attributes: true, attributeFilter: ["class"] })
    return () => obs.disconnect()
  }, [])

  const tileUrl = isDark
    ? layer === "light"
      ? "https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png"
      : "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
    : TILES[layer].url

  const fitView = () => {
    if (!map) return
    const pts = points.map((p) => [p.lat!, p.lng!] as [number, number])
    if (pts.length) map.fitBounds(pts, { padding: [50, 50], maxZoom: 13 })
  }

  const ctrlCard =
    "w-[52px] flex flex-col items-center gap-0.5 py-2 rounded-xl bg-white dark:bg-night-800 shadow-md border border-gray-200/70 dark:border-white/10 text-gray-600 dark:text-[#d9d9d9] hover:text-teal-700 dark:hover:text-teal-400 hover:bg-gray-50 dark:hover:bg-white/10 transition"

  return (
    <div className="relative w-full h-full">
      <MapContainer
        ref={setMap}
        center={[36.5, -119.5]}
        zoom={6}
        className="w-full h-full z-0"
        zoomControl={false}
        attributionControl={false}
      >
        <TileLayer key={tileUrl} url={tileUrl} />
        <FitBounds points={points} />
        <MarkerClusterGroup
          iconCreateFunction={createClusterIcon}
          showCoverageOnHover={false}
          spiderfyOnMaxZoom
          maxClusterRadius={55}
          chunkedLoading
        >
          {points.map((p) => (
            <Marker
              key={p.place_id}
              position={[p.lat!, p.lng!]}
              icon={createPinIcon(p.lead_score ?? null)}
              ref={(ref) => {
                if (ref) markerRefs.current[p.place_id] = ref
              }}
              eventHandlers={{ click: () => onSelect(p.place_id) }}
            >
              <Popup minWidth={220}>
                <PopupBody
                  practice={p}
                  onAnalyze={onAnalyze}
                  analyzing={analyzingIds?.has(p.place_id) ?? false}
                />
              </Popup>
            </Marker>
          ))}
        </MarkerClusterGroup>
      </MapContainer>

      {/* Search this area */}
      {onSearchArea && (
        <button
          onClick={onSearchArea}
          className="absolute top-4 left-[452px] z-[15] inline-flex items-center gap-2 px-4 py-2.5 rounded-full bg-white dark:bg-night-800 shadow-md border border-gray-200/70 dark:border-white/10 text-sm font-medium text-gray-700 dark:text-[#d9d9d9] hover:bg-gray-50 dark:hover:bg-white/10 transition"
        >
          <Crosshair className="w-4 h-4 text-teal-600 dark:text-teal-400" />
          Search this area
        </button>
      )}

      {/* Map controls */}
      <div className="absolute top-4 right-4 z-[15] flex flex-col items-end gap-2">
        <div className="flex flex-col rounded-xl bg-white dark:bg-night-800 shadow-md border border-gray-200/70 dark:border-white/10 overflow-hidden">
          <button
            onClick={() => map?.zoomIn()}
            className="w-[52px] h-11 grid place-items-center text-gray-600 dark:text-[#d9d9d9] hover:text-teal-700 dark:hover:text-teal-400 hover:bg-gray-50 dark:hover:bg-white/10 transition"
            title="Zoom in"
          >
            <Plus className="w-4 h-4" />
          </button>
          <div className="h-px bg-gray-200/70 dark:bg-white/10" />
          <button
            onClick={() => map?.zoomOut()}
            className="w-[52px] h-11 grid place-items-center text-gray-600 dark:text-[#d9d9d9] hover:text-teal-700 dark:hover:text-teal-400 hover:bg-gray-50 dark:hover:bg-white/10 transition"
            title="Zoom out"
          >
            <Minus className="w-4 h-4" />
          </button>
        </div>
        <button onClick={fitView} className={ctrlCard} title="Fit all leads in view">
          <Maximize2 className="w-4 h-4" />
          <span className="text-[10px] font-medium">Fit view</span>
        </button>
        <button
          className={ctrlCard}
          title="Draw an area"
        >
          <PenLine className="w-4 h-4" />
          <span className="text-[10px] font-medium">Draw</span>
        </button>
        <button
          onClick={() => setLayer((l) => (l === "street" ? "light" : "street"))}
          className={ctrlCard}
          title="Toggle base map"
        >
          <Layers className="w-4 h-4" />
          <span className="text-[10px] font-medium">Layers</span>
        </button>
      </div>

      {/* Legend */}
      <div className="absolute bottom-4 right-4 z-[15] rounded-xl bg-white/95 dark:bg-night-800 shadow-md border border-gray-200/70 dark:border-white/10 px-4 py-3 w-44">
        <p className="text-sm font-semibold text-gray-900 dark:text-white mb-2">Lead score</p>
        <ul className="space-y-1.5">
          {LEGEND.map((row) => (
            <li key={row.label} className="flex items-center gap-2 text-xs text-gray-600 dark:text-[#d9d9d9]">
              <span
                className="w-2.5 h-2.5 rounded-full"
                style={{ backgroundColor: row.color }}
              />
              {row.label}
            </li>
          ))}
        </ul>
        <p className="text-[10px] text-gray-400 dark:text-gray-500 mt-2.5 leading-tight">
          © OpenStreetMap contributors
        </p>
      </div>
    </div>
  )
}
