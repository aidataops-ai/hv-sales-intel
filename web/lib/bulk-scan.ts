// City + specialty presets used by the Bulk Scan modal.
// Cities come from the H&V ICP launch-priority lists where available and
// from major-metro population data for the rest of the US. Specialties
// are pulled from the per-vertical PDFs. Edit here to tune sweeps.

export type StateCode =
  | "AL" | "AK" | "AZ" | "AR" | "CA" | "CO" | "CT" | "DE" | "DC"
  | "FL" | "GA" | "HI" | "ID" | "IL" | "IN" | "IA" | "KS" | "KY"
  | "LA" | "ME" | "MD" | "MA" | "MI" | "MN" | "MS" | "MO" | "MT"
  | "NE" | "NV" | "NH" | "NJ" | "NM" | "NY" | "NC" | "ND" | "OH"
  | "OK" | "OR" | "PA" | "RI" | "SC" | "SD" | "TN" | "TX" | "UT"
  | "VT" | "VA" | "WA" | "WV" | "WI" | "WY"

export const STATE_LABELS: Record<StateCode, string> = {
  AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas",
  CA: "California", CO: "Colorado", CT: "Connecticut", DE: "Delaware",
  DC: "District of Columbia", FL: "Florida", GA: "Georgia", HI: "Hawaii",
  ID: "Idaho", IL: "Illinois", IN: "Indiana", IA: "Iowa", KS: "Kansas",
  KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland",
  MA: "Massachusetts", MI: "Michigan", MN: "Minnesota", MS: "Mississippi",
  MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada",
  NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico", NY: "New York",
  NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma",
  OR: "Oregon", PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina",
  SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah",
  VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia",
  WI: "Wisconsin", WY: "Wyoming",
}

export const STATE_CITIES: Record<StateCode, string[]> = {
  AL: ["Birmingham", "Montgomery", "Huntsville", "Mobile", "Tuscaloosa", "Hoover", "Auburn"],
  AK: ["Anchorage", "Fairbanks", "Juneau", "Wasilla"],
  AZ: ["Phoenix", "Tucson", "Mesa", "Chandler", "Scottsdale", "Glendale", "Gilbert", "Tempe", "Peoria"],
  AR: ["Little Rock", "Fort Smith", "Fayetteville", "Springdale", "Jonesboro", "Rogers"],
  CA: [
    "Los Angeles", "San Francisco", "San Diego", "San Jose",
    "Sacramento", "Fresno", "Long Beach", "Oakland",
    "Anaheim", "Riverside", "Irvine", "Bakersfield",
    "Beverly Hills", "Santa Monica", "Pasadena", "Berkeley",
  ],
  CO: ["Denver", "Colorado Springs", "Aurora", "Fort Collins", "Lakewood", "Boulder", "Thornton"],
  CT: ["Hartford", "Stamford", "New Haven", "Bridgeport", "Norwalk", "Waterbury", "Greenwich"],
  DE: ["Wilmington", "Dover", "Newark", "Middletown"],
  DC: ["Washington"],
  FL: [
    "Miami", "Miami Beach", "Coral Gables", "Aventura",
    "Fort Lauderdale", "Boca Raton", "Palm Beach",
    "Naples", "Sarasota", "Fort Myers", "Lakewood Ranch",
    "Tampa", "St. Petersburg", "Clearwater",
    "Orlando", "Winter Park", "Dr. Phillips",
    "Jacksonville", "Ponte Vedra", "St. Johns",
    "Tallahassee", "Gainesville", "Daytona Beach", "Pensacola", "Key West",
  ],
  GA: ["Atlanta", "Savannah", "Augusta", "Columbus", "Athens", "Sandy Springs", "Roswell", "Marietta", "Macon"],
  HI: ["Honolulu", "Hilo", "Pearl City", "Waipahu", "Kailua"],
  ID: ["Boise", "Meridian", "Nampa", "Idaho Falls", "Pocatello", "Caldwell"],
  IL: ["Chicago", "Aurora", "Naperville", "Joliet", "Rockford", "Springfield", "Peoria", "Evanston"],
  IN: ["Indianapolis", "Fort Wayne", "Evansville", "South Bend", "Carmel", "Bloomington", "Fishers"],
  IA: ["Des Moines", "Cedar Rapids", "Davenport", "Sioux City", "Iowa City", "Waterloo"],
  KS: ["Wichita", "Overland Park", "Kansas City", "Topeka", "Olathe", "Lawrence"],
  KY: ["Louisville", "Lexington", "Bowling Green", "Owensboro", "Covington"],
  LA: ["New Orleans", "Baton Rouge", "Shreveport", "Lafayette", "Lake Charles", "Metairie"],
  ME: ["Portland", "Lewiston", "Bangor", "Augusta", "South Portland"],
  MD: ["Baltimore", "Annapolis", "Rockville", "Bethesda", "Gaithersburg", "Frederick", "Silver Spring"],
  MA: ["Boston", "Worcester", "Springfield", "Lowell", "Cambridge", "Newton", "Quincy", "Brookline"],
  MI: ["Detroit", "Grand Rapids", "Warren", "Sterling Heights", "Lansing", "Ann Arbor", "Flint", "Troy"],
  MN: ["Minneapolis", "Saint Paul", "Rochester", "Duluth", "Bloomington", "Plymouth", "Maple Grove"],
  MS: ["Jackson", "Gulfport", "Southaven", "Hattiesburg", "Biloxi"],
  MO: ["Kansas City", "Saint Louis", "Springfield", "Columbia", "Independence", "Lee's Summit"],
  MT: ["Billings", "Missoula", "Great Falls", "Bozeman", "Helena"],
  NE: ["Omaha", "Lincoln", "Bellevue", "Grand Island"],
  NV: ["Las Vegas", "Henderson", "Reno", "North Las Vegas", "Sparks"],
  NH: ["Manchester", "Nashua", "Concord", "Derry"],
  NJ: ["Newark", "Jersey City", "Paterson", "Elizabeth", "Edison", "Trenton", "Camden", "Princeton"],
  NM: ["Albuquerque", "Santa Fe", "Las Cruces", "Rio Rancho", "Roswell"],
  NY: [
    "New York", "Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island",
    "Buffalo", "Rochester", "Syracuse", "Albany", "Yonkers", "White Plains",
    "New Rochelle", "Long Island", "Westchester",
  ],
  NC: ["Charlotte", "Raleigh", "Greensboro", "Durham", "Winston-Salem", "Cary", "Asheville", "Wilmington"],
  ND: ["Fargo", "Bismarck", "Grand Forks", "Minot"],
  OH: [
    "Columbus", "Cleveland", "Cincinnati", "Toledo",
    "Akron", "Dayton", "Youngstown", "Canton",
    "Parma", "Lorain", "Hamilton", "Springfield",
  ],
  OK: ["Oklahoma City", "Tulsa", "Norman", "Edmond", "Broken Arrow", "Lawton"],
  OR: ["Portland", "Salem", "Eugene", "Hillsboro", "Bend", "Beaverton", "Medford"],
  PA: [
    "Philadelphia", "Pittsburgh", "Allentown", "Erie", "Lancaster",
    "Harrisburg", "Reading", "Scranton", "Bethlehem", "King of Prussia",
  ],
  RI: ["Providence", "Warwick", "Cranston", "Pawtucket", "East Providence"],
  SC: ["Columbia", "Charleston", "Greenville", "Mount Pleasant", "North Charleston", "Rock Hill"],
  SD: ["Sioux Falls", "Rapid City", "Aberdeen"],
  TN: ["Nashville", "Memphis", "Knoxville", "Chattanooga", "Clarksville", "Franklin", "Murfreesboro"],
  TX: [
    "Houston", "Dallas", "Austin", "San Antonio", "Fort Worth",
    "El Paso", "Plano", "Arlington", "Corpus Christi", "Lubbock",
    "Frisco", "Sugar Land", "The Woodlands", "Irving", "McKinney",
    "Garland", "Amarillo", "Brownsville", "Killeen", "Pasadena",
  ],
  UT: ["Salt Lake City", "West Valley City", "Provo", "Sandy", "Orem", "Lehi", "Park City"],
  VT: ["Burlington", "South Burlington", "Rutland", "Montpelier"],
  VA: ["Virginia Beach", "Norfolk", "Chesapeake", "Richmond", "Arlington", "Alexandria", "Fairfax", "Reston"],
  WA: ["Seattle", "Spokane", "Tacoma", "Vancouver", "Bellevue", "Everett", "Kent", "Redmond"],
  WV: ["Charleston", "Huntington", "Morgantown", "Parkersburg"],
  WI: ["Milwaukee", "Madison", "Green Bay", "Kenosha", "Racine", "Appleton", "Waukesha"],
  WY: ["Cheyenne", "Casper", "Laramie", "Gillette"],
}

export type Vertical = "medical" | "dental" | "alf_nh" | "hotel_resort" | "medspa_wellness"

export const VERTICAL_LABELS: Record<Vertical, string> = {
  medical: "Medical practices",
  dental: "Dental practices",
  alf_nh: "Assisted living / Nursing homes",
  hotel_resort: "Hotels / Resorts",
  medspa_wellness: "MedSpa / Spa / Wellness",
}

// Default search phrase per vertical — used to auto-fill the State sweep
// template when the rep picks a vertical.
export const VERTICAL_BASE_QUERIES: Record<Vertical, string> = {
  medical: "medical clinics",
  dental: "dental clinics",
  alf_nh: "assisted living facilities",
  hotel_resort: "hotels and resorts",
  medspa_wellness: "medspas and wellness clinics",
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

export function templateForVertical(v: Vertical): string {
  return `${VERTICAL_BASE_QUERIES[v]} in {city}, {state}`
}

/**
 * Build the list of search queries for a "State sweep" run across one or
 * more states. Template uses {city}, {state}, {stateLabel} placeholders.
 */
export function buildStateSweepQueries(opts: {
  template: string
  states: StateCode[]
}): string[] {
  const out: string[] = []
  for (const state of opts.states) {
    const cities = STATE_CITIES[state] ?? []
    const stateLabel = STATE_LABELS[state]
    for (const city of cities) {
      const q = opts.template
        .replace(/\{city\}/g, city)
        .replace(/\{state\}/g, state)
        .replace(/\{stateLabel\}/g, stateLabel)
        .trim()
      if (q) out.push(q)
    }
  }
  return out
}

/**
 * Build the cartesian (state × city × specialty) list for a "Specialty
 * grid" run. Query shape: "<specialty> in <city>, <state>".
 */
export function buildSpecialtyGridQueries(opts: {
  states: StateCode[]
  specialties: string[]
}): string[] {
  const out: string[] = []
  for (const state of opts.states) {
    const cities = STATE_CITIES[state] ?? []
    for (const specialty of opts.specialties) {
      for (const city of cities) {
        out.push(`${specialty} in ${city}, ${state}`)
      }
    }
  }
  return out
}

export function totalCitiesForStates(states: StateCode[]): number {
  return states.reduce((acc, s) => acc + (STATE_CITIES[s]?.length ?? 0), 0)
}
