// City + specialty presets used by the Bulk Scan modal.
// Cities come from the H&V ICP launch-priority lists; specialties are pulled
// from the per-vertical PDFs. Edit here to tune sweeps.

export type StateCode = "FL" | "TX" | "CA" | "NY" | "OH"

export const STATE_LABELS: Record<StateCode, string> = {
  FL: "Florida",
  TX: "Texas",
  CA: "California",
  NY: "New York",
  OH: "Ohio",
}

export const STATE_CITIES: Record<StateCode, string[]> = {
  FL: [
    "Miami", "Miami Beach", "Coral Gables", "Aventura",
    "Fort Lauderdale", "Boca Raton", "Palm Beach",
    "Naples", "Sarasota", "Fort Myers", "Lakewood Ranch",
    "Tampa", "St. Petersburg", "Clearwater",
    "Orlando", "Winter Park", "Dr. Phillips",
    "Jacksonville", "Ponte Vedra", "St. Johns",
    "Tallahassee", "Gainesville", "Daytona Beach", "Pensacola", "Key West",
  ],
  TX: [
    "Houston", "Dallas", "Austin", "San Antonio", "Fort Worth",
    "El Paso", "Plano", "Arlington", "Corpus Christi", "Lubbock",
    "Frisco", "Sugar Land", "The Woodlands",
  ],
  CA: [
    "Los Angeles", "San Francisco", "San Diego", "San Jose",
    "Sacramento", "Fresno", "Long Beach", "Oakland",
    "Anaheim", "Riverside", "Irvine", "Beverly Hills", "Santa Monica",
  ],
  NY: [
    "New York", "Manhattan", "Brooklyn", "Queens", "Bronx",
    "Buffalo", "Rochester", "Syracuse", "Albany", "Yonkers", "White Plains",
  ],
  OH: [
    "Columbus", "Cleveland", "Cincinnati", "Toledo",
    "Akron", "Dayton", "Youngstown", "Canton",
  ],
}

export type Vertical = "medical" | "dental" | "alf_nh" | "hotel_resort" | "medspa_wellness"

export const VERTICAL_LABELS: Record<Vertical, string> = {
  medical: "Medical practices",
  dental: "Dental practices",
  alf_nh: "Assisted living / Nursing homes",
  hotel_resort: "Hotels / Resorts",
  medspa_wellness: "MedSpa / Spa / Wellness",
}

export const SPECIALTIES_BY_VERTICAL: Record<Vertical, string[]> = {
  medical: [
    "primary care clinic",
    "family medicine practice",
    "internal medicine practice",
    "psychiatry practice",
    "mental health clinic",
    "chiropractic clinic",
    "urgent care",
  ],
  dental: [
    "dental clinic",
    "pediatric dentist",
    "orthodontist",
    "endodontist",
    "periodontist",
    "oral surgeon",
    "cosmetic dentist",
    "implant dentist",
  ],
  alf_nh: [
    "assisted living facility",
    "memory care facility",
    "nursing home",
    "skilled nursing facility",
    "senior living community",
  ],
  hotel_resort: [
    "boutique hotel",
    "resort",
    "vacation rental management",
    "spa resort",
  ],
  medspa_wellness: [
    "medspa",
    "aesthetics clinic",
    "wellness clinic",
    "day spa",
    "anti-aging clinic",
  ],
}

/**
 * Build the list of search queries for a "State sweep" run.
 * Template uses `{city}` and `{state}` placeholders (state is the full label).
 *   e.g. "dental clinics in {city}, FL" → "dental clinics in Miami, FL", …
 */
export function buildStateSweepQueries(opts: {
  template: string
  state: StateCode
}): string[] {
  const cities = STATE_CITIES[opts.state] ?? []
  const stateLabel = STATE_LABELS[opts.state]
  return cities
    .map((city) =>
      opts.template
        .replace(/\{city\}/g, city)
        .replace(/\{state\}/g, opts.state)
        .replace(/\{stateLabel\}/g, stateLabel)
        .trim(),
    )
    .filter(Boolean)
}

/**
 * Build the cartesian (city × specialty) list for a "Specialty grid" run.
 * Query shape: "<specialty> in <city>, <state>".
 */
export function buildSpecialtyGridQueries(opts: {
  state: StateCode
  specialties: string[]
}): string[] {
  const cities = STATE_CITIES[opts.state] ?? []
  const out: string[] = []
  for (const specialty of opts.specialties) {
    for (const city of cities) {
      out.push(`${specialty} in ${city}, ${opts.state}`)
    }
  }
  return out
}
