"use client"

import { useEffect, useRef, useState } from "react"
import { MapContainer, TileLayer, Marker, Popup, useMap } from "react-leaflet"
import MarkerClusterGroup from "react-leaflet-cluster"
import L from "leaflet"
import "leaflet/dist/leaflet.css"
import { Plus, Minus, Maximize2, PenLine, Layers, Crosshair } from "lucide-react"
import type { Practice } from "@/lib/types"

// Lead-score → marker colour. Mirrors the legend buckets exactly.
function scoreColor(score: number | null): string {
  if (score == null) return "#cbd5e1" // slate-300 — unscored
  if (score >= 80) return "#0f766e" // teal-700
  if (score >= 60) return "#0d9488" // teal-600
  if (score >= 40) return "#f59e0b" // amber-500
  return "#fda4af" // rose-300 — 0–39
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
}

const LEGEND = [
  { color: "#0f766e", label: "80 – 100" },
  { color: "#0d9488", label: "60 – 79" },
  { color: "#f59e0b", label: "40 – 59" },
  { color: "#fda4af", label: "0 – 39" },
]

export default function MapView({
  practices,
  selectedId,
  onSelect,
  onSearchArea,
}: MapViewProps) {
  const [map, setMap] = useState<L.Map | null>(null)
  const [layer, setLayer] = useState<"street" | "light">("street")
  const markerRefs = useRef<Record<string, L.Marker>>({})

  const points = practices.filter((p) => p.lat != null && p.lng != null)

  useEffect(() => {
    if (selectedId && markerRefs.current[selectedId]) {
      markerRefs.current[selectedId].openPopup()
    }
  }, [selectedId])

  const fitView = () => {
    if (!map) return
    const pts = points.map((p) => [p.lat!, p.lng!] as [number, number])
    if (pts.length) map.fitBounds(pts, { padding: [50, 50], maxZoom: 13 })
  }

  const ctrlCard =
    "w-[52px] flex flex-col items-center gap-0.5 py-2 rounded-xl bg-white shadow-md border border-gray-200/70 text-gray-600 hover:text-teal-700 hover:bg-gray-50 transition"

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
        <TileLayer url={TILES[layer].url} />
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
              <Popup>
                <strong className="font-serif">{p.name}</strong>
                <br />
                {p.address}
              </Popup>
            </Marker>
          ))}
        </MarkerClusterGroup>
      </MapContainer>

      {/* Search this area */}
      {onSearchArea && (
        <button
          onClick={onSearchArea}
          className="absolute top-4 left-[452px] z-[15] inline-flex items-center gap-2 px-4 py-2.5 rounded-full bg-white shadow-md border border-gray-200/70 text-sm font-medium text-gray-700 hover:bg-gray-50 transition"
        >
          <Crosshair className="w-4 h-4 text-teal-600" />
          Search this area
        </button>
      )}

      {/* Map controls */}
      <div className="absolute top-4 right-4 z-[15] flex flex-col items-end gap-2">
        <div className="flex flex-col rounded-xl bg-white shadow-md border border-gray-200/70 overflow-hidden">
          <button
            onClick={() => map?.zoomIn()}
            className="w-[52px] h-11 grid place-items-center text-gray-600 hover:text-teal-700 hover:bg-gray-50 transition"
            title="Zoom in"
          >
            <Plus className="w-4 h-4" />
          </button>
          <div className="h-px bg-gray-200/70" />
          <button
            onClick={() => map?.zoomOut()}
            className="w-[52px] h-11 grid place-items-center text-gray-600 hover:text-teal-700 hover:bg-gray-50 transition"
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
      <div className="absolute bottom-4 right-4 z-[15] rounded-xl bg-white/95 shadow-md border border-gray-200/70 px-4 py-3 w-44">
        <p className="text-sm font-semibold text-gray-900 mb-2">Lead score</p>
        <ul className="space-y-1.5">
          {LEGEND.map((row) => (
            <li key={row.label} className="flex items-center gap-2 text-xs text-gray-600">
              <span
                className="w-2.5 h-2.5 rounded-full"
                style={{ backgroundColor: row.color }}
              />
              {row.label}
            </li>
          ))}
        </ul>
        <p className="text-[10px] text-gray-400 mt-2.5 leading-tight">
          © OpenStreetMap contributors
        </p>
      </div>
    </div>
  )
}
