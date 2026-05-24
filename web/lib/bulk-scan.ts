// City + specialty presets used by the Bulk Scan modal.
// Cities come from the Apex ICP launch-priority lists where available and
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

// State → incorporated city list. Significantly expanded from the original
// "top 5-10 per state" set to cover most incorporated municipalities by
// population. Not perfectly exhaustive (CA actually has ~483, this is ~250+)
// — the modal pairs this with a "Custom cities" textarea so any rep can
// supplement the defaults for their territory.
export const STATE_CITIES: Record<StateCode, string[]> = {
  AL: [
    "Birmingham", "Montgomery", "Huntsville", "Mobile", "Tuscaloosa",
    "Hoover", "Auburn", "Decatur", "Madison", "Florence", "Dothan",
    "Gadsden", "Vestavia Hills", "Prattville", "Phenix City", "Pelham",
    "Bessemer", "Anniston", "Northport", "Trussville", "Athens",
    "Daphne", "Opelika", "Enterprise", "Fairhope", "Homewood",
  ],
  AK: ["Anchorage", "Fairbanks", "Juneau", "Wasilla", "Sitka", "Ketchikan", "Kenai", "Palmer", "Soldotna", "Kodiak"],
  AZ: [
    "Phoenix", "Tucson", "Mesa", "Chandler", "Scottsdale", "Glendale",
    "Gilbert", "Tempe", "Peoria", "Surprise", "Yuma", "Avondale",
    "Goodyear", "Flagstaff", "Buckeye", "Lake Havasu City", "Casa Grande",
    "Sierra Vista", "Maricopa", "Oro Valley", "Prescott", "Prescott Valley",
    "Apache Junction", "Marana", "El Mirage", "Queen Creek", "Kingman",
    "San Luis", "Sahuarita", "Florence", "Fountain Hills", "Nogales",
    "Douglas", "Eloy", "Payson", "Show Low", "Sedona", "Cottonwood",
    "Bullhead City", "Globe", "Safford", "Chino Valley",
  ],
  AR: [
    "Little Rock", "Fort Smith", "Fayetteville", "Springdale", "Jonesboro",
    "Rogers", "North Little Rock", "Conway", "Bentonville", "Pine Bluff",
    "Hot Springs", "Benton", "Texarkana", "Sherwood", "Jacksonville",
    "Russellville", "Bella Vista", "West Memphis", "Paragould", "Cabot",
    "Searcy", "Van Buren", "El Dorado", "Maumelle", "Bryant", "Siloam Springs",
  ],
  CA: [
    // Los Angeles County
    "Los Angeles", "Long Beach", "Glendale", "Santa Clarita", "Lancaster",
    "Palmdale", "Pomona", "Pasadena", "Torrance", "El Monte", "Downey",
    "Inglewood", "West Covina", "Norwalk", "Burbank", "Compton", "South Gate",
    "Carson", "Santa Monica", "Whittier", "Hawthorne", "Alhambra", "Lakewood",
    "Bellflower", "Baldwin Park", "Lynwood", "Redondo Beach", "Pico Rivera",
    "Montebello", "Monterey Park", "Gardena", "Diamond Bar", "Paramount",
    "Glendora", "Huntington Park", "La Habra", "Cerritos", "Cudahy", "Maywood",
    "Bell Gardens", "Bell", "Vernon", "Industry", "Commerce", "Walnut",
    "Beverly Hills", "Culver City", "Manhattan Beach", "El Segundo",
    "Hermosa Beach", "La Verne", "San Dimas", "Covina", "West Hollywood",
    "Calabasas", "Agoura Hills", "Westlake Village", "Claremont", "Duarte",
    "La Cañada Flintridge", "La Mirada", "La Puente", "Lawndale", "Lomita",
    "Malibu", "Monrovia", "Palos Verdes Estates", "Rancho Palos Verdes",
    "Rolling Hills Estates", "Rosemead", "San Fernando", "San Gabriel",
    "San Marino", "Santa Fe Springs", "Signal Hill", "South Pasadena",
    "Temple City", "Sierra Madre", "Arcadia",
    // Orange County
    "Anaheim", "Santa Ana", "Irvine", "Huntington Beach", "Garden Grove",
    "Fullerton", "Orange", "Costa Mesa", "Mission Viejo", "Westminster",
    "Newport Beach", "Lake Forest", "Buena Park", "Tustin", "Yorba Linda",
    "San Clemente", "Laguna Niguel", "Fountain Valley", "Placentia",
    "Aliso Viejo", "Cypress", "Brea", "Stanton", "Rancho Santa Margarita",
    "Dana Point", "Laguna Hills", "San Juan Capistrano", "Laguna Beach",
    "Seal Beach", "La Habra", "Los Alamitos",
    // Inland Empire (Riverside + San Bernardino)
    "Riverside", "Moreno Valley", "Fontana", "Rancho Cucamonga", "Ontario",
    "San Bernardino", "Corona", "Murrieta", "Temecula", "Jurupa Valley",
    "Menifee", "Hesperia", "Victorville", "Indio", "Rialto", "Chino",
    "Chino Hills", "Upland", "Redlands", "Hemet", "Lake Elsinore",
    "Apple Valley", "Highland", "Yucaipa", "Colton", "Eastvale", "Wildomar",
    "Beaumont", "Banning", "Coachella", "Palm Desert", "Palm Springs",
    "Cathedral City", "La Quinta", "Loma Linda", "Twentynine Palms",
    // San Diego County
    "San Diego", "Chula Vista", "Oceanside", "Escondido", "Carlsbad",
    "El Cajon", "Vista", "San Marcos", "Encinitas", "National City",
    "La Mesa", "Santee", "Poway", "Coronado", "Imperial Beach",
    "Lemon Grove", "Solana Beach", "Del Mar",
    // Bay Area
    "San Francisco", "San Jose", "Oakland", "Fremont", "Sunnyvale",
    "Hayward", "Santa Clara", "Concord", "Berkeley", "Daly City",
    "Richmond", "Vallejo", "San Mateo", "Antioch", "Redwood City",
    "Mountain View", "Alameda", "Pleasanton", "Walnut Creek", "San Leandro",
    "Livermore", "Milpitas", "Union City", "Palo Alto", "Cupertino",
    "Napa", "Petaluma", "Pittsburg", "South San Francisco", "Brentwood",
    "Newark", "Dublin", "Pleasant Hill", "Martinez", "Belmont", "Burlingame",
    "San Bruno", "San Rafael", "Novato", "San Carlos", "Foster City",
    "Menlo Park", "Atherton", "Half Moon Bay", "Saratoga", "Los Gatos",
    "Los Altos", "Morgan Hill", "Gilroy", "Campbell", "Hercules", "Lafayette",
    "Orinda", "Moraga", "Tiburon", "Mill Valley", "Sausalito",
    "Saint Helena", "Sonoma", "Healdsburg", "Windsor", "Rohnert Park",
    "Santa Rosa", "Cotati", "American Canyon",
    // Sacramento Valley
    "Sacramento", "Elk Grove", "Roseville", "Folsom", "Citrus Heights",
    "Rancho Cordova", "Davis", "Woodland", "West Sacramento", "Yuba City",
    "Marysville", "Auburn", "Rocklin", "Lincoln", "El Dorado Hills",
    // Central Valley
    "Fresno", "Bakersfield", "Stockton", "Modesto", "Visalia", "Clovis",
    "Tracy", "Manteca", "Turlock", "Merced", "Lodi", "Ceres", "Madera",
    "Hanford", "Tulare", "Porterville", "Delano", "Atwater", "Los Banos",
    "Galt", "Patterson", "Oakdale", "Selma", "Reedley", "Sanger",
    "Wasco", "Shafter", "Arvin",
    // Central Coast
    "Salinas", "Santa Maria", "Santa Barbara", "San Luis Obispo", "Paso Robles",
    "Goleta", "Lompoc", "Watsonville", "Santa Cruz", "Hollister", "Monterey",
    "Pacific Grove", "Carmel-by-the-Sea", "Seaside", "Marina", "King City",
    "Atascadero", "Morro Bay", "Arroyo Grande", "Grover Beach", "Pismo Beach",
    "Ventura", "Oxnard", "Thousand Oaks", "Simi Valley", "Camarillo",
    "Moorpark", "Fillmore", "Santa Paula", "Port Hueneme",
    // North Coast / Far North
    "Eureka", "Arcata", "Fortuna", "Crescent City", "Ukiah", "Fort Bragg",
    "Chico", "Redding", "Anderson", "Red Bluff", "Susanville", "Oroville",
    "Paradise", "Mount Shasta", "Yreka",
  ],
  CO: [
    "Denver", "Colorado Springs", "Aurora", "Fort Collins", "Lakewood",
    "Boulder", "Thornton", "Arvada", "Westminster", "Pueblo", "Centennial",
    "Greeley", "Longmont", "Loveland", "Broomfield", "Castle Rock",
    "Grand Junction", "Commerce City", "Parker", "Littleton", "Northglenn",
    "Brighton", "Englewood", "Wheat Ridge", "Lafayette", "Windsor",
    "Erie", "Evans", "Golden", "Louisville", "Durango", "Montrose",
    "Cañon City", "Glenwood Springs", "Sterling", "Steamboat Springs",
    "Vail", "Aspen", "Breckenridge",
  ],
  CT: [
    "Bridgeport", "Stamford", "New Haven", "Hartford", "Waterbury",
    "Norwalk", "Danbury", "New Britain", "Bristol", "Meriden",
    "West Hartford", "Greenwich", "Fairfield", "Hamden", "Manchester",
    "East Hartford", "Stratford", "Milford", "Naugatuck", "Newington",
    "Cheshire", "Trumbull", "Glastonbury", "Wallingford", "Wethersfield",
    "Shelton", "Branford", "Vernon", "Windsor", "Middletown", "Torrington",
    "Enfield", "Bloomfield", "Farmington", "Simsbury", "South Windsor",
    "Mansfield", "New London", "Groton", "Norwich", "Old Saybrook",
  ],
  DE: [
    "Wilmington", "Dover", "Newark", "Middletown", "Smyrna", "Milford",
    "Seaford", "Georgetown", "Elsmere", "New Castle", "Lewes", "Rehoboth Beach",
    "Bear", "Hockessin", "Glasgow", "Pike Creek", "Brookside",
  ],
  DC: ["Washington"],
  FL: [
    // South Florida (Miami-Dade, Broward, Palm Beach)
    "Miami", "Miami Beach", "Coral Gables", "Aventura", "Hialeah",
    "Hialeah Gardens", "Doral", "Homestead", "Kendall", "Pinecrest",
    "Key Biscayne", "North Miami", "North Miami Beach", "Sunny Isles Beach",
    "Bal Harbour", "Bay Harbor Islands", "Surfside", "Miami Lakes",
    "Miami Gardens", "Miami Springs", "Cutler Bay", "Palmetto Bay",
    "South Miami", "Sweetwater", "Opa-locka", "Florida City",
    "Fort Lauderdale", "Hollywood", "Pembroke Pines", "Coral Springs",
    "Miramar", "Plantation", "Sunrise", "Davie", "Pompano Beach",
    "Deerfield Beach", "Tamarac", "Lauderhill", "Weston", "Margate",
    "Coconut Creek", "Cooper City", "Parkland", "Hallandale Beach",
    "Oakland Park", "North Lauderdale", "Lauderdale Lakes", "Wilton Manors",
    "Dania Beach", "West Park", "Lighthouse Point",
    "West Palm Beach", "Boca Raton", "Boynton Beach", "Delray Beach",
    "Palm Beach", "Palm Beach Gardens", "Wellington", "Jupiter", "Royal Palm Beach",
    "Lake Worth Beach", "Riviera Beach", "Greenacres", "Palm Springs",
    // Southwest Florida
    "Naples", "Marco Island", "Bonita Springs", "Estero", "Fort Myers",
    "Cape Coral", "Lehigh Acres", "Sanibel", "Punta Gorda", "Port Charlotte",
    "North Port", "Venice", "Sarasota", "Bradenton", "Palmetto",
    "Lakewood Ranch", "Anna Maria", "Holmes Beach", "Bradenton Beach",
    "Englewood", "Charlotte Harbor",
    // Tampa Bay
    "Tampa", "St. Petersburg", "Clearwater", "Largo", "Brandon",
    "Pinellas Park", "Palm Harbor", "Dunedin", "Safety Harbor", "Oldsmar",
    "Tarpon Springs", "Seminole", "Wesley Chapel", "New Port Richey",
    "Port Richey", "Hudson", "Spring Hill", "Plant City", "Riverview",
    "Apollo Beach", "Lutz", "Ruskin", "Sun City Center", "Land O' Lakes",
    "Temple Terrace", "Town 'n' Country",
    // Central Florida (Orlando metro)
    "Orlando", "Winter Park", "Kissimmee", "Lake Mary", "Sanford",
    "Altamonte Springs", "Apopka", "Casselberry", "Longwood", "Maitland",
    "Oviedo", "Winter Springs", "Winter Garden", "Ocoee", "Windermere",
    "Celebration", "Dr. Phillips", "Lake Buena Vista", "Lake Nona",
    "Davenport", "Clermont", "Mount Dora", "Eustis", "Tavares", "Leesburg",
    "The Villages", "Lady Lake", "Wildwood", "Bushnell",
    // North-Central
    "Gainesville", "Ocala", "Lake City", "Crystal River", "Inverness",
    "Brooksville", "Dade City", "Zephyrhills",
    // Northeast Florida
    "Jacksonville", "Jacksonville Beach", "Atlantic Beach", "Neptune Beach",
    "Ponte Vedra", "Ponte Vedra Beach", "Saint Augustine", "Saint Johns",
    "Orange Park", "Fleming Island", "Middleburg", "Green Cove Springs",
    "Palatka", "Palm Coast", "Bunnell", "Daytona Beach", "Daytona Beach Shores",
    "Ormond Beach", "Port Orange", "New Smyrna Beach", "DeLand", "DeBary",
    "Edgewater", "Deltona",
    // Treasure Coast / Space Coast
    "Port St. Lucie", "Stuart", "Jensen Beach", "Vero Beach", "Sebastian",
    "Fort Pierce", "Hobe Sound", "Tequesta", "Palm City",
    "Melbourne", "Palm Bay", "Cocoa", "Cocoa Beach", "Rockledge",
    "Titusville", "Merritt Island", "Satellite Beach", "Indian Harbour Beach",
    // Panhandle
    "Tallahassee", "Pensacola", "Pensacola Beach", "Gulf Breeze", "Navarre",
    "Fort Walton Beach", "Destin", "Crestview", "Niceville", "Panama City",
    "Panama City Beach", "Lynn Haven", "Apalachicola", "Quincy", "Marianna",
    // Keys
    "Key West", "Marathon", "Key Largo", "Islamorada", "Tavernier",
  ],
  GA: [
    "Atlanta", "Augusta", "Columbus", "Savannah", "Macon", "Athens",
    "Sandy Springs", "Roswell", "Johns Creek", "Albany", "Warner Robins",
    "Marietta", "Valdosta", "Smyrna", "Brookhaven", "Dunwoody",
    "Alpharetta", "Peachtree City", "East Point", "Milton", "Newnan",
    "Gainesville", "Hinesville", "Rome", "Tucker", "Kennesaw", "Decatur",
    "Lawrenceville", "Duluth", "Carrollton", "Statesboro", "LaGrange",
    "Stockbridge", "Forest Park", "Suwanee", "Mableton", "Acworth",
    "Powder Springs", "Cartersville", "Calhoun", "Dalton", "Douglasville",
    "Lithia Springs", "Norcross", "Snellville", "Lilburn", "Tifton",
    "Dublin", "Pooler", "Brunswick", "Saint Marys", "Saint Simons",
    "Jekyll Island", "Sea Island", "Tybee Island",
  ],
  HI: [
    "Honolulu", "Pearl City", "Hilo", "Kailua", "Waipahu", "Kaneohe",
    "Mililani", "Kahului", "Ewa Beach", "Wahiawa", "Kihei", "Lahaina",
    "Wailuku", "Kapolei", "Kailua-Kona", "Kapaa", "Lihue", "Princeville",
    "Hanalei", "Waimea", "Haleiwa", "Aiea", "Waianae", "Waikiki",
  ],
  ID: [
    "Boise", "Meridian", "Nampa", "Idaho Falls", "Pocatello", "Caldwell",
    "Coeur d'Alene", "Twin Falls", "Lewiston", "Post Falls", "Rexburg",
    "Moscow", "Eagle", "Kuna", "Mountain Home", "Chubbuck", "Hayden",
    "Jerome", "Sandpoint", "Garden City", "Star", "Burley", "Ammon",
  ],
  IL: [
    "Chicago", "Aurora", "Joliet", "Naperville", "Rockford", "Elgin",
    "Springfield", "Peoria", "Champaign", "Waukegan", "Cicero", "Bloomington",
    "Arlington Heights", "Schaumburg", "Evanston", "Bolingbrook", "Decatur",
    "Palatine", "Skokie", "Des Plaines", "Orland Park", "Tinley Park",
    "Oak Lawn", "Berwyn", "Mount Prospect", "Wheaton", "Hoffman Estates",
    "Oak Park", "Downers Grove", "Elmhurst", "Glenview", "DeKalb", "Lombard",
    "Belleville", "Buffalo Grove", "Bartlett", "Crystal Lake", "Carol Stream",
    "Streamwood", "Quincy", "Urbana", "Plainfield", "Hanover Park",
    "Carpentersville", "Wheeling", "Park Ridge", "Addison", "Calumet City",
    "Northbrook", "Romeoville", "Pekin", "Galesburg", "Highland Park",
    "Burbank", "Edwardsville", "Glen Ellyn", "Lake in the Hills", "Granite City",
    "Mundelein", "Belvidere", "Alton", "Vernon Hills", "Algonquin",
    "Lockport", "Saint Charles", "Geneva", "Batavia", "Lake Zurich",
    "Lansing", "Woodridge", "Niles", "Bensenville", "Grayslake", "Round Lake",
    "Frankfort", "Morton Grove", "Wood Dale", "Westmont", "Lincoln",
    "Loves Park", "Freeport", "Mokena", "New Lenox", "Glendale Heights",
    "East Saint Louis", "Highland", "Marion", "Carbondale", "Effingham",
    "Charleston", "Mattoon",
  ],
  IN: [
    "Indianapolis", "Fort Wayne", "Evansville", "South Bend", "Carmel",
    "Fishers", "Bloomington", "Hammond", "Gary", "Lafayette", "Muncie",
    "Noblesville", "Greenwood", "Anderson", "Elkhart", "Mishawaka",
    "Lawrence", "Jeffersonville", "Columbus", "Portage", "Westfield",
    "New Albany", "Goshen", "Michigan City", "West Lafayette", "Marion",
    "Crown Point", "Plainfield", "Brownsburg", "Schererville", "Munster",
    "Highland", "Hobart", "Valparaiso", "Avon", "Bedford", "Zionsville",
    "Logansport", "Vincennes", "Crawfordsville", "Seymour", "Greenfield",
    "Madison", "Shelbyville", "Franklin", "Auburn", "New Castle", "Frankfort",
  ],
  IA: [
    "Des Moines", "Cedar Rapids", "Davenport", "Sioux City", "Iowa City",
    "Waterloo", "Council Bluffs", "Ames", "West Des Moines", "Dubuque",
    "Ankeny", "Urbandale", "Cedar Falls", "Marion", "Bettendorf",
    "Mason City", "Marshalltown", "Clinton", "Burlington", "Ottumwa",
    "Fort Dodge", "Muscatine", "Coralville", "Johnston", "Clive", "Indianola",
    "Newton", "Altoona", "Storm Lake", "Spencer", "Boone", "Pella",
  ],
  KS: [
    "Wichita", "Overland Park", "Kansas City", "Olathe", "Topeka",
    "Lawrence", "Shawnee", "Manhattan", "Lenexa", "Salina", "Hutchinson",
    "Leavenworth", "Leawood", "Garden City", "Junction City", "Emporia",
    "Derby", "Prairie Village", "Liberal", "Hays", "Pittsburg", "Gardner",
    "Newton", "Great Bend", "McPherson", "Coffeyville", "Arkansas City",
    "Atchison", "Mission", "Andover",
  ],
  KY: [
    "Louisville", "Lexington", "Bowling Green", "Owensboro", "Covington",
    "Hopkinsville", "Richmond", "Florence", "Georgetown", "Henderson",
    "Elizabethtown", "Nicholasville", "Jeffersontown", "Frankfort",
    "Paducah", "Independence", "Radcliff", "Ashland", "Madisonville",
    "Murray", "Erlanger", "Winchester", "Saint Matthews", "Fort Thomas",
    "Danville", "Newport", "Shively", "Glasgow", "Berea", "Shelbyville",
    "Mount Washington", "Bardstown", "Campbellsville", "Lawrenceburg",
  ],
  LA: [
    "New Orleans", "Baton Rouge", "Shreveport", "Lafayette", "Lake Charles",
    "Kenner", "Bossier City", "Monroe", "Alexandria", "Houma", "Marrero",
    "Metairie", "New Iberia", "Slidell", "Hammond", "Sulphur", "Natchitoches",
    "Gretna", "Opelousas", "Mandeville", "Ruston", "Pineville", "Zachary",
    "Thibodaux", "Crowley", "Morgan City", "Minden", "Bogalusa", "Covington",
    "Bastrop", "Jennings", "DeRidder", "Eunice", "Abbeville", "Plaquemine",
  ],
  ME: [
    "Portland", "Lewiston", "Bangor", "South Portland", "Auburn",
    "Biddeford", "Sanford", "Saco", "Augusta", "Westbrook", "Waterville",
    "Brunswick", "Scarborough", "Falmouth", "Gorham", "Cape Elizabeth",
    "Yarmouth", "Old Orchard Beach", "Kennebunk", "Wells", "York",
    "Kittery", "Camden", "Rockland", "Ellsworth", "Belfast", "Bar Harbor",
    "Presque Isle", "Caribou", "Calais",
  ],
  MD: [
    "Baltimore", "Frederick", "Rockville", "Gaithersburg", "Bowie", "Hagerstown",
    "Annapolis", "College Park", "Salisbury", "Laurel", "Greenbelt",
    "Cumberland", "Westminster", "Hyattsville", "Takoma Park", "Bethesda",
    "Silver Spring", "Wheaton", "Germantown", "Aspen Hill", "Potomac",
    "Glen Burnie", "Columbia", "Towson", "Ellicott City", "Catonsville",
    "Dundalk", "Essex", "Pikesville", "Owings Mills", "Severna Park",
    "Pasadena", "Bel Air", "Aberdeen", "Havre de Grace", "Edgewood",
    "Easton", "Cambridge", "Ocean City", "Ocean Pines", "Berlin",
    "La Plata", "Waldorf", "Lexington Park", "California", "Leonardtown",
  ],
  MA: [
    "Boston", "Worcester", "Springfield", "Cambridge", "Lowell", "Brockton",
    "Quincy", "Lynn", "New Bedford", "Fall River", "Newton", "Lawrence",
    "Somerville", "Framingham", "Haverhill", "Waltham", "Malden", "Brookline",
    "Plymouth", "Medford", "Taunton", "Chicopee", "Weymouth", "Revere",
    "Peabody", "Methuen", "Barnstable", "Pittsfield", "Attleboro", "Everett",
    "Salem", "Westfield", "Leominster", "Fitchburg", "Beverly", "Holyoke",
    "Marlborough", "Woburn", "Chelsea", "Braintree", "Watertown", "Arlington",
    "Andover", "Natick", "Needham", "Wellesley", "Belmont", "Reading",
    "Stoneham", "Winchester", "Lexington", "Concord", "Burlington", "Wilmington",
    "Tewksbury", "Billerica", "Chelmsford", "Bedford", "Sudbury", "Acton",
    "Westborough", "Northborough", "Shrewsbury", "Holden", "Hopkinton",
    "Milton", "Dedham", "Norwood", "Walpole", "Sharon", "Canton", "Hingham",
    "Cohasset", "Scituate", "Marshfield", "Duxbury", "Hanover", "Norwell",
    "Easton", "Mansfield", "Foxborough",
  ],
  MI: [
    "Detroit", "Grand Rapids", "Warren", "Sterling Heights", "Ann Arbor",
    "Lansing", "Flint", "Dearborn", "Livonia", "Westland", "Troy",
    "Farmington Hills", "Kalamazoo", "Wyoming", "Southfield", "Rochester Hills",
    "Taylor", "Saint Clair Shores", "Pontiac", "Royal Oak", "Novi", "Dearborn Heights",
    "Battle Creek", "Saginaw", "Roseville", "Kentwood", "East Lansing",
    "Portage", "Midland", "Lincoln Park", "Muskegon", "Bay City",
    "Holland", "Jackson", "Eastpointe", "Burton", "Madison Heights",
    "Oak Park", "Birmingham", "Walled Lake", "Wixom", "South Lyon",
    "Brighton", "Howell", "Hartland", "Plymouth", "Canton", "Northville",
    "Auburn Hills", "Berkley", "Bloomfield Hills", "West Bloomfield",
    "Commerce", "Romulus", "Trenton", "Wyandotte", "Allen Park",
    "Garden City", "Inkster", "Riverview", "Southgate", "Woodhaven",
    "Mount Pleasant", "Big Rapids", "Marquette", "Traverse City", "Petoskey",
    "Sault Sainte Marie", "Cadillac",
  ],
  MN: [
    "Minneapolis", "Saint Paul", "Rochester", "Bloomington", "Duluth",
    "Brooklyn Park", "Plymouth", "Maple Grove", "Woodbury", "Saint Cloud",
    "Eagan", "Eden Prairie", "Coon Rapids", "Burnsville", "Blaine",
    "Lakeville", "Minnetonka", "Apple Valley", "Edina", "Saint Louis Park",
    "Mankato", "Maplewood", "Moorhead", "Shakopee", "Cottage Grove",
    "Richfield", "Roseville", "Inver Grove Heights", "Andover", "Brooklyn Center",
    "Savage", "Fridley", "Oakdale", "Chanhassen", "Prior Lake", "Ramsey",
    "Hopkins", "Faribault", "Owatonna", "Winona", "Hibbing", "Bemidji",
    "Crookston", "Worthington", "Marshall", "New Ulm", "Stillwater",
  ],
  MS: [
    "Jackson", "Gulfport", "Southaven", "Hattiesburg", "Biloxi", "Meridian",
    "Tupelo", "Olive Branch", "Greenville", "Horn Lake", "Pearl", "Clinton",
    "Madison", "Starkville", "Ridgeland", "Columbus", "Vicksburg", "Pascagoula",
    "Brandon", "Oxford", "Gautier", "Laurel", "Long Beach", "Ocean Springs",
    "Natchez", "Bay Saint Louis", "Greenwood", "McComb", "Cleveland",
    "Picayune", "Brookhaven", "Yazoo City", "Indianola",
  ],
  MO: [
    "Kansas City", "Saint Louis", "Springfield", "Independence", "Columbia",
    "Lee's Summit", "O'Fallon", "Saint Joseph", "Saint Charles", "Saint Peters",
    "Blue Springs", "Florissant", "Joplin", "Chesterfield", "Jefferson City",
    "Cape Girardeau", "Wildwood", "University City", "Ballwin", "Raytown",
    "Liberty", "Wentzville", "Mehlville", "Kirkwood", "Maryland Heights",
    "Hazelwood", "Gladstone", "Grandview", "Belton", "Webster Groves",
    "Rolla", "Sedalia", "Branson", "Warrensburg", "Hannibal", "Sikeston",
    "Poplar Bluff", "Kennett", "Fulton", "Lebanon", "Marshall", "Carthage",
    "Maryville", "Excelsior Springs", "Festus", "Nixa", "Ozark",
  ],
  MT: [
    "Billings", "Missoula", "Great Falls", "Bozeman", "Butte", "Helena",
    "Kalispell", "Havre", "Anaconda", "Belgrade", "Livingston", "Whitefish",
    "Sidney", "Miles City", "Lewistown", "Glendive", "Polson", "Hamilton",
  ],
  NE: [
    "Omaha", "Lincoln", "Bellevue", "Grand Island", "Kearney", "Fremont",
    "Hastings", "Norfolk", "North Platte", "Papillion", "La Vista",
    "Columbus", "Scottsbluff", "South Sioux City", "Beatrice", "Lexington",
    "Gering", "York", "Alliance", "Blair", "McCook", "Nebraska City",
  ],
  NV: [
    "Las Vegas", "Henderson", "North Las Vegas", "Reno", "Sparks",
    "Carson City", "Fernley", "Elko", "Mesquite", "Boulder City",
    "Fallon", "Winnemucca", "West Wendover", "Ely", "Yerington",
    "Pahrump", "Spring Valley", "Sunrise Manor", "Paradise", "Summerlin",
    "Lake Tahoe", "Incline Village", "Stateline",
  ],
  NH: [
    "Manchester", "Nashua", "Concord", "Derry", "Dover", "Rochester",
    "Salem", "Merrimack", "Londonderry", "Hudson", "Bedford", "Keene",
    "Portsmouth", "Goffstown", "Laconia", "Hampton", "Milford", "Durham",
    "Exeter", "Windham", "Lebanon", "Pelham", "Hanover", "Newmarket",
    "Hooksett", "Stratham", "Wolfeboro", "Conway", "Plymouth", "Franklin",
    "Berlin", "Claremont", "Somersworth",
  ],
  NJ: [
    "Newark", "Jersey City", "Paterson", "Elizabeth", "Edison", "Woodbridge",
    "Lakewood", "Toms River", "Hamilton", "Trenton", "Clifton", "Camden",
    "Brick", "Cherry Hill", "Passaic", "Middletown", "Union City", "Old Bridge",
    "Gloucester", "East Orange", "Bayonne", "Franklin", "North Bergen", "Vineland",
    "Union", "Piscataway", "New Brunswick", "Jackson", "Wayne", "Irvington",
    "Parsippany-Troy Hills", "Howell", "Perth Amboy", "Hoboken", "Plainfield",
    "Bloomfield", "West New York", "East Brunswick", "West Orange", "Sayreville",
    "Hackensack", "Kearny", "Linden", "Atlantic City", "Fort Lee", "Princeton",
    "Asbury Park", "Long Branch", "Red Bank", "Freehold", "Manalapan",
    "Marlboro", "Holmdel", "Wall", "Belmar", "Spring Lake", "Point Pleasant",
    "Morristown", "Madison", "Summit", "Westfield", "Cranford", "Maplewood",
    "Millburn", "Short Hills", "Livingston", "Verona", "Montclair", "Glen Ridge",
    "Ridgewood", "Tenafly", "Englewood", "Cliffside Park", "Fairview",
    "Carlstadt", "Lyndhurst", "Rutherford", "Nutley", "Belleville",
  ],
  NM: [
    "Albuquerque", "Las Cruces", "Rio Rancho", "Santa Fe", "Roswell",
    "Farmington", "Hobbs", "Clovis", "Alamogordo", "Carlsbad", "Gallup",
    "Deming", "Los Alamos", "Chaparral", "Sunland Park", "Las Vegas",
    "Portales", "Los Lunas", "Silver City", "Espanola", "Lovington",
    "Belen", "Artesia", "Anthony", "Ruidoso", "Truth or Consequences",
    "Taos", "Tucumcari", "Bernalillo", "Aztec", "Bloomfield",
  ],
  NY: [
    // NYC + boroughs
    "New York", "Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island",
    // Long Island (Nassau + Suffolk)
    "Hempstead", "Levittown", "Freeport", "Hicksville", "Long Beach",
    "Glen Cove", "Garden City", "Massapequa", "Massapequa Park", "Mineola",
    "Westbury", "Roosevelt", "Uniondale", "Rockville Centre", "Baldwin",
    "Valley Stream", "Lynbrook", "Bellmore", "Wantagh", "Merrick", "Seaford",
    "Plainview", "Syosset", "Jericho", "Old Westbury", "Manhasset", "Great Neck",
    "Port Washington", "Roslyn", "Oceanside", "East Meadow", "West Hempstead",
    "Babylon", "Bay Shore", "West Babylon", "Brentwood", "Central Islip",
    "Smithtown", "Commack", "Huntington", "Huntington Station", "Northport",
    "Stony Brook", "Port Jefferson", "Patchogue", "Bayport", "Sayville",
    "Islip", "Lindenhurst", "Amityville", "Copiague", "Riverhead", "Southampton",
    "East Hampton", "Sag Harbor", "Greenport", "Montauk",
    // Hudson Valley + Westchester
    "Yonkers", "Mount Vernon", "New Rochelle", "White Plains", "Scarsdale",
    "Bronxville", "Eastchester", "Pelham", "Larchmont", "Mamaroneck",
    "Rye", "Port Chester", "Tarrytown", "Sleepy Hollow", "Ossining",
    "Peekskill", "Yorktown Heights", "Pleasantville", "Chappaqua", "Armonk",
    "Bedford", "Mount Kisco", "Katonah", "Croton-on-Hudson",
    "Nyack", "Nanuet", "Spring Valley", "New City", "Suffern", "Pearl River",
    "Newburgh", "Middletown", "Goshen", "Monroe", "Poughkeepsie", "Beacon",
    "Fishkill", "Wappingers Falls", "Hyde Park", "Rhinebeck", "Kingston",
    "New Paltz", "Saugerties", "Woodstock",
    // Capital region
    "Albany", "Schenectady", "Troy", "Saratoga Springs", "Cohoes", "Watervliet",
    "Rensselaer", "Glens Falls", "Mechanicville", "Amsterdam",
    // Central / Western NY
    "Syracuse", "Utica", "Rome", "Binghamton", "Endicott", "Ithaca",
    "Cortland", "Auburn", "Geneva", "Oswego", "Watertown", "Oneida",
    "Rochester", "Brighton", "Greece", "Irondequoit", "Pittsford", "Penfield",
    "Webster", "Henrietta", "Fairport", "Victor", "Canandaigua", "Newark",
    "Lyons", "Buffalo", "Tonawanda", "Niagara Falls", "Lockport", "Lackawanna",
    "Cheektowaga", "Amherst", "West Seneca", "Williamsville", "Hamburg",
    "Orchard Park", "East Aurora", "Jamestown", "Olean", "Dunkirk",
    "Fredonia", "Salamanca",
  ],
  NC: [
    "Charlotte", "Raleigh", "Greensboro", "Durham", "Winston-Salem",
    "Fayetteville", "Cary", "Wilmington", "High Point", "Concord",
    "Asheville", "Greenville", "Gastonia", "Jacksonville", "Chapel Hill",
    "Rocky Mount", "Burlington", "Hickory", "Wilson", "Huntersville",
    "Apex", "Indian Trail", "Mooresville", "Wake Forest", "Cornelius",
    "Salisbury", "New Bern", "Kannapolis", "Sanford", "Garner", "Holly Springs",
    "Monroe", "Goldsboro", "Matthews", "Statesville", "Morrisville", "Thomasville",
    "Asheboro", "Mint Hill", "Fuquay-Varina", "Knightdale", "Carrboro",
    "Lumberton", "Kinston", "Boone", "Hendersonville", "Pinehurst", "Southern Pines",
    "Eden", "Lexington", "Lenoir", "Shelby", "Clemmons", "Kernersville",
    "Stallings", "Davidson", "Harrisburg", "Waxhaw", "Albemarle",
    "Hope Mills", "Spring Lake", "Smithfield", "Clayton", "Mebane",
    "Tarboro", "Roanoke Rapids", "Henderson", "Oxford", "Reidsville",
    "Elizabeth City", "Edenton", "Manteo", "Nags Head", "Kill Devil Hills",
    "Outer Banks",
  ],
  ND: [
    "Fargo", "Bismarck", "Grand Forks", "Minot", "West Fargo", "Williston",
    "Dickinson", "Mandan", "Jamestown", "Wahpeton", "Devils Lake", "Valley City",
  ],
  OH: [
    "Columbus", "Cleveland", "Cincinnati", "Toledo", "Akron", "Dayton",
    "Parma", "Canton", "Youngstown", "Lorain", "Hamilton", "Springfield",
    "Kettering", "Elyria", "Lakewood", "Cuyahoga Falls", "Middletown",
    "Newark", "Mansfield", "Mentor", "Beavercreek", "Cleveland Heights",
    "Strongsville", "Dublin", "Fairfield", "Findlay", "Warren", "Lancaster",
    "Lima", "Huber Heights", "Westerville", "Marion", "Grove City", "Reynoldsburg",
    "Delaware", "Brunswick", "Upper Arlington", "Stow", "Gahanna", "North Olmsted",
    "Westlake", "North Royalton", "Bowling Green", "Garfield Heights", "Shaker Heights",
    "Massillon", "Mayfield Heights", "Euclid", "Medina", "Sandusky", "Solon",
    "Sidney", "Defiance", "Tiffin", "Wooster", "Athens", "Chillicothe", "Zanesville",
    "Portsmouth", "Steubenville", "Marietta", "Cambridge", "East Liverpool",
    "Salem", "Ashland", "Bucyrus", "Norwalk", "Sandusky", "Fremont",
    "Painesville", "Willoughby", "Eastlake", "Wickliffe", "Twinsburg",
    "Hudson", "Aurora", "Bay Village", "Avon", "Avon Lake", "North Ridgeville",
    "Berea", "Olmsted Falls", "Brecksville", "Broadview Heights", "Independence",
    "Macedonia", "Streetsboro", "Tallmadge", "Norton", "Barberton",
  ],
  OK: [
    "Oklahoma City", "Tulsa", "Norman", "Broken Arrow", "Edmond", "Lawton",
    "Moore", "Midwest City", "Enid", "Stillwater", "Muskogee", "Bartlesville",
    "Owasso", "Shawnee", "Ponca City", "Yukon", "Ardmore", "Bixby", "Duncan",
    "Sapulpa", "Bethany", "Mustang", "Sand Springs", "Del City", "Altus",
    "El Reno", "McAlester", "Claremore", "Durant", "Tahlequah", "Chickasha",
    "Glenpool", "Miami", "Woodward", "Elk City", "Ada", "Choctaw",
  ],
  OR: [
    "Portland", "Salem", "Eugene", "Gresham", "Hillsboro", "Beaverton",
    "Bend", "Medford", "Springfield", "Corvallis", "Albany", "Tigard",
    "Lake Oswego", "Keizer", "Grants Pass", "Oregon City", "McMinnville",
    "Redmond", "Tualatin", "West Linn", "Forest Grove", "Newberg",
    "Wilsonville", "Roseburg", "Klamath Falls", "Ashland", "Milwaukie",
    "Pendleton", "Hermiston", "The Dalles", "Coos Bay", "Lebanon",
    "Sherwood", "Astoria", "Newport", "Florence", "Lincoln City",
    "Cannon Beach", "Seaside", "Hood River", "Sandy", "Happy Valley",
    "Canby", "Estacada",
  ],
  PA: [
    "Philadelphia", "Pittsburgh", "Allentown", "Erie", "Reading", "Scranton",
    "Bethlehem", "Lancaster", "Harrisburg", "York", "Wilkes-Barre", "Altoona",
    "Chester", "Williamsport", "Easton", "Lebanon", "Hazleton", "New Castle",
    "Norristown", "Pottstown", "Phoenixville", "King of Prussia", "Plymouth Meeting",
    "Conshohocken", "Bryn Mawr", "Wayne", "Radnor", "Villanova", "Ardmore",
    "Drexel Hill", "Springfield", "Upper Darby", "Media", "Newtown Square",
    "West Chester", "Exton", "Downingtown", "Coatesville", "Kennett Square",
    "Levittown", "Bristol", "Doylestown", "Newtown", "Yardley", "Langhorne",
    "Quakertown", "Sellersville", "Perkasie", "Souderton", "Lansdale",
    "North Wales", "Hatboro", "Warminster", "Horsham", "Willow Grove",
    "Jenkintown", "Glenside", "Cheltenham", "Abington", "Penn Wynne",
    "State College", "Pottsville", "Carlisle", "Mechanicsburg", "Camp Hill",
    "Hershey", "Lemoyne", "Annville", "Cleona", "Palmyra", "Cornwall",
    "Hummelstown", "Middletown", "Elizabethtown", "Mount Joy", "Manheim",
    "Ephrata", "Lititz", "New Holland", "Strasburg", "Quarryville",
    "Greensburg", "Latrobe", "Indiana", "Johnstown", "DuBois", "Clearfield",
    "Bradford", "Warren", "Meadville", "Sharon", "Hermitage", "Butler",
    "Beaver Falls", "Aliquippa", "Ambridge", "Sewickley", "McKees Rocks",
    "McKeesport", "Monroeville", "Penn Hills", "Plum", "Murrysville",
    "Bethel Park", "Mt. Lebanon", "Upper St. Clair", "South Park", "Pleasant Hills",
  ],
  RI: [
    "Providence", "Warwick", "Cranston", "Pawtucket", "East Providence",
    "Woonsocket", "Coventry", "Cumberland", "North Providence", "South Kingstown",
    "West Warwick", "Johnston", "North Kingstown", "Newport", "Bristol",
    "Westerly", "Smithfield", "Lincoln", "Central Falls", "Portsmouth",
    "Barrington", "Middletown", "East Greenwich", "Tiverton", "Narragansett",
    "Burrillville", "North Smithfield", "Warren", "Scituate", "Glocester",
  ],
  SC: [
    "Columbia", "Charleston", "North Charleston", "Mount Pleasant", "Rock Hill",
    "Greenville", "Summerville", "Sumter", "Goose Creek", "Hilton Head Island",
    "Florence", "Spartanburg", "Myrtle Beach", "Aiken", "Anderson", "Greer",
    "Mauldin", "Greenwood", "North Augusta", "Easley", "Hanahan", "Lexington",
    "Beaufort", "Conway", "West Columbia", "North Myrtle Beach", "Cayce",
    "Bluffton", "Forest Acres", "Orangeburg", "Camden", "Clemson", "Newberry",
    "Simpsonville", "Fountain Inn", "Travelers Rest", "Hartsville", "Lancaster",
    "Bennettsville", "Walhalla", "Seneca", "Gaffney",
  ],
  SD: [
    "Sioux Falls", "Rapid City", "Aberdeen", "Brookings", "Watertown",
    "Mitchell", "Yankton", "Pierre", "Huron", "Vermillion", "Spearfish",
    "Brandon", "Box Elder", "Madison", "Belle Fourche", "Sturgis",
  ],
  TN: [
    "Nashville", "Memphis", "Knoxville", "Chattanooga", "Clarksville",
    "Murfreesboro", "Franklin", "Jackson", "Johnson City", "Bartlett",
    "Hendersonville", "Kingsport", "Collierville", "Smyrna", "Cleveland",
    "Brentwood", "Germantown", "Columbia", "La Vergne", "Gallatin",
    "Cookeville", "Mount Juliet", "Lebanon", "Morristown", "Oak Ridge",
    "Maryville", "Bristol", "Spring Hill", "Farragut", "Goodlettsville",
    "Shelbyville", "Tullahoma", "Dyersburg", "Sevierville", "Pigeon Forge",
    "Gatlinburg", "Greeneville", "Athens", "McMinnville", "Crossville",
    "Springfield", "Dickson", "Lewisburg", "Lawrenceburg", "Pulaski",
    "Manchester", "Winchester", "Fayetteville", "Union City", "Paris",
  ],
  TX: [
    // DFW
    "Dallas", "Fort Worth", "Arlington", "Plano", "Irving", "Garland",
    "Frisco", "McKinney", "Grand Prairie", "Mesquite", "Carrollton", "Denton",
    "Richardson", "Lewisville", "Allen", "Flower Mound", "North Richland Hills",
    "Mansfield", "Rowlett", "Euless", "Bedford", "Grapevine", "Cedar Hill",
    "Wylie", "DeSoto", "Coppell", "Keller", "Hurst", "The Colony", "Little Elm",
    "Burleson", "Duncanville", "Haltom City", "Lancaster", "Saginaw",
    "Watauga", "Mansfield", "Glenn Heights", "Forney", "Royse City",
    "Sherman", "Denison", "Greenville", "Paris", "Texarkana", "Tyler",
    "Longview", "Marshall", "Kilgore", "Henderson", "Jacksonville",
    // Houston metro
    "Houston", "Sugar Land", "Pasadena", "Pearland", "Spring", "The Woodlands",
    "League City", "Conroe", "Baytown", "Missouri City", "Atascocita",
    "Cypress", "Katy", "Friendswood", "Sienna", "Galveston", "Texas City",
    "La Porte", "Deer Park", "Webster", "Stafford", "Rosenberg", "Tomball",
    "Magnolia", "Humble", "Kingwood", "Jersey Village", "Bellaire",
    "West University Place", "Beaumont", "Port Arthur", "Orange", "Lufkin",
    "Nacogdoches",
    // Austin metro
    "Austin", "Round Rock", "Cedar Park", "Pflugerville", "Georgetown",
    "Leander", "Hutto", "Buda", "Kyle", "San Marcos", "Lockhart", "Bastrop",
    "Elgin", "Taylor", "Manor", "Lakeway", "Bee Cave", "West Lake Hills",
    "Rollingwood",
    // San Antonio metro
    "San Antonio", "New Braunfels", "Schertz", "Cibolo", "Selma",
    "Universal City", "Live Oak", "Converse", "Helotes", "Boerne",
    "Seguin", "Floresville", "Pleasanton", "Castroville", "Hondo",
    // Coast
    "Corpus Christi", "Portland", "Aransas Pass", "Rockport", "Kingsville",
    "Alice", "Beeville",
    // Valley
    "McAllen", "Brownsville", "Harlingen", "Edinburg", "Mission",
    "Pharr", "San Benito", "Weslaco", "Mercedes", "Donna", "Alamo",
    "San Juan", "Raymondville", "Eagle Pass", "Del Rio", "Laredo",
    // West Texas
    "El Paso", "Socorro", "Horizon City", "Sun City", "Lubbock",
    "Amarillo", "Midland", "Odessa", "Big Spring", "San Angelo", "Abilene",
    "Wichita Falls", "Burkburnett", "Vernon", "Stephenville", "Brownwood",
    "Waco", "Temple", "Killeen", "Harker Heights", "Belton", "Copperas Cove",
    "Salado", "Bryan", "College Station",
  ],
  UT: [
    "Salt Lake City", "West Valley City", "West Jordan", "Provo", "Orem",
    "Sandy", "Ogden", "St. George", "Layton", "Taylorsville", "South Jordan",
    "Lehi", "Logan", "Murray", "Draper", "Bountiful", "Riverton", "Herriman",
    "Spanish Fork", "Roy", "Pleasant Grove", "Tooele", "Cedar City",
    "Springville", "Cottonwood Heights", "Kaysville", "Holladay",
    "American Fork", "Syracuse", "Saratoga Springs", "Eagle Mountain",
    "Clearfield", "Midvale", "Washington", "Farmington", "Park City",
    "Heber City", "Vernal", "Brigham City", "Hurricane", "South Salt Lake",
    "Mapleton", "Highland", "Alpine", "Lindon", "Smithfield", "North Logan",
  ],
  VT: [
    "Burlington", "South Burlington", "Rutland", "Essex Junction", "Colchester",
    "Bennington", "Brattleboro", "Saint Albans", "Barre", "Montpelier",
    "Winooski", "Williston", "Middlebury", "Springfield", "St. Johnsbury",
    "Hartford", "Newport", "Vergennes", "Stowe", "Manchester", "Killington",
    "Woodstock",
  ],
  VA: [
    "Virginia Beach", "Norfolk", "Chesapeake", "Richmond", "Newport News",
    "Alexandria", "Hampton", "Roanoke", "Portsmouth", "Suffolk", "Lynchburg",
    "Harrisonburg", "Charlottesville", "Danville", "Manassas", "Petersburg",
    "Fredericksburg", "Winchester", "Salem", "Staunton", "Hopewell",
    "Waynesboro", "Colonial Heights", "Radford", "Bristol", "Falls Church",
    "Martinsville", "Fairfax", "Manassas Park", "Williamsburg", "Poquoson",
    "Buena Vista", "Galax", "Norton", "Lexington", "Emporia", "Covington",
    // Northern VA (CDPs / unincorporated but commonly searched)
    "Arlington", "McLean", "Vienna", "Reston", "Herndon", "Ashburn",
    "Sterling", "Leesburg", "Centreville", "Chantilly", "Springfield",
    "Burke", "Annandale", "Tysons", "Great Falls", "Falls Church",
    "Dale City", "Woodbridge", "Manassas", "Gainesville", "Bristow",
    "Haymarket", "Warrenton", "Culpeper", "Front Royal", "Stafford",
    "Triangle", "Quantico", "Dumfries", "Lake Ridge", "Lorton",
    "Mount Vernon", "Fort Belvoir",
    // Tidewater extras
    "Smithfield", "Yorktown", "Gloucester", "Mathews", "Cape Charles",
    // Western VA
    "Blacksburg", "Christiansburg", "Pulaski", "Wytheville", "Marion",
    "Abingdon", "Big Stone Gap",
  ],
  WA: [
    "Seattle", "Spokane", "Tacoma", "Vancouver", "Bellevue", "Kent",
    "Everett", "Renton", "Federal Way", "Spokane Valley", "Yakima", "Bellingham",
    "Kennewick", "Auburn", "Kirkland", "Pasco", "Marysville", "Lakewood",
    "Redmond", "Shoreline", "Richland", "Sammamish", "Burien", "Olympia",
    "Lacey", "Edmonds", "Bremerton", "Puyallup", "Bothell", "Lynnwood",
    "Issaquah", "Wenatchee", "Mount Vernon", "Mukilteo", "Bainbridge Island",
    "Mercer Island", "Maple Valley", "Walla Walla", "University Place",
    "Pullman", "Des Moines", "SeaTac", "Tukwila", "Covington", "Camas",
    "Battle Ground", "Aberdeen", "Centralia", "Chehalis", "Anacortes",
    "Oak Harbor", "Port Angeles", "Sequim", "Port Townsend", "Friday Harbor",
    "Ellensburg", "Moses Lake", "Ephrata", "Sunnyside", "Selah", "Toppenish",
    "Snohomish", "Monroe", "Lake Stevens", "Arlington", "Stanwood",
    "Gig Harbor", "Steilacoom", "DuPont", "Sumner", "Bonney Lake", "Enumclaw",
    "Black Diamond", "Snoqualmie", "North Bend", "Carnation", "Duvall",
  ],
  WV: [
    "Charleston", "Huntington", "Morgantown", "Parkersburg", "Wheeling",
    "Weirton", "Fairmont", "Martinsburg", "Beckley", "Clarksburg",
    "South Charleston", "St. Albans", "Vienna", "Bluefield", "Moundsville",
    "Bridgeport", "Oak Hill", "Dunbar", "Elkins", "Nitro", "Hurricane",
    "Princeton", "Buckhannon", "Keyser", "Lewisburg", "Ranson",
  ],
  WI: [
    "Milwaukee", "Madison", "Green Bay", "Kenosha", "Racine", "Appleton",
    "Waukesha", "Eau Claire", "Oshkosh", "Janesville", "West Allis", "La Crosse",
    "Sheboygan", "Wauwatosa", "Fond du Lac", "New Berlin", "Brookfield",
    "Beloit", "Greenfield", "Franklin", "Menomonee Falls", "Oak Creek",
    "Manitowoc", "West Bend", "Sun Prairie", "Superior", "Stevens Point",
    "Neenah", "Watertown", "Marshfield", "Wisconsin Rapids", "Hartford",
    "Mequon", "Cudahy", "Pleasant Prairie", "Caledonia", "Onalaska", "Middleton",
    "Fitchburg", "De Pere", "Plover", "Hudson", "Pewaukee", "Whitewater",
    "Beaver Dam", "Two Rivers", "Cedarburg", "Port Washington", "Grafton",
    "Germantown", "Menasha", "Kaukauna", "Little Chute", "Marinette",
    "Rhinelander", "Wausau", "Merrill", "Antigo", "Shawano", "Sturgeon Bay",
    "Door County", "Bayfield", "Hayward",
  ],
  WY: [
    "Cheyenne", "Casper", "Laramie", "Gillette", "Rock Springs", "Sheridan",
    "Green River", "Evanston", "Riverton", "Jackson", "Cody", "Rawlins",
    "Lander", "Torrington", "Powell", "Douglas", "Worland", "Buffalo",
    "Wheatland", "Newcastle", "Thermopolis", "Pinedale", "Kemmerer", "Saratoga",
  ],
}

// Bulk Scan picks a vertical purely to drive query phrasing in the modal.
// The canonical ICP vertical (used by the scorer) is separate — psychiatry /
// mental_health practices are still classified by the analyzer as the
// "medical" ICP vertical per the Apex ICP doc, which keeps Vertical fit
// scoring intact.
export type Vertical =
  | "medical"
  | "mental_health"
  | "dental"
  | "alf_nh"
  | "hotel_resort"
  | "medspa_wellness"

export const VERTICAL_LABELS: Record<Vertical, string> = {
  medical: "Medical practices (primary care, internal, family)",
  mental_health: "Mental health (psychiatry, therapy, behavioral)",
  dental: "Dental practices",
  alf_nh: "Assisted living / Nursing homes",
  hotel_resort: "Hotels / Resorts",
  medspa_wellness: "MedSpa / Spa / Wellness",
}

// Default search phrase per vertical — used to auto-fill the State sweep
// template when the rep picks a vertical.
export const VERTICAL_BASE_QUERIES: Record<Vertical, string> = {
  medical: "medical clinics",
  mental_health: "mental health clinics",
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
    "chiropractic clinic",
    "urgent care",
    "pediatric clinic",
    "geriatric clinic",
  ],
  mental_health: [
    "mental health clinic",
    "psychiatry practice",
    "behavioral health center",
    "counseling center",
    "therapist office",
    "psychologist office",
    "addiction recovery clinic",
    "trauma therapy clinic",
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
 * Parse the "Custom cities" textarea into a per-state extras map.
 * Accepted formats per line (any of):
 *   - "Vernon, CA"        (city + 2-letter state)
 *   - "Vernon CA"         (whitespace separator)
 *   - "California: Vernon, Maywood, Bell, ..."  (state-prefixed, then comma-list)
 * Lines that don't carry a state code are silently dropped.
 */
export function parseCustomCities(raw: string): Partial<Record<StateCode, string[]>> {
  const out: Partial<Record<StateCode, string[]>> = {}
  const labelToCode = new Map<string, StateCode>()
  for (const [code, label] of Object.entries(STATE_LABELS) as [StateCode, string][]) {
    labelToCode.set(label.toLowerCase(), code)
  }
  const validCodes = new Set(Object.keys(STATE_LABELS))

  function push(state: StateCode, city: string) {
    const c = city.trim()
    if (!c) return
    if (!out[state]) out[state] = []
    if (!out[state]!.includes(c)) out[state]!.push(c)
  }

  for (const rawLine of raw.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line) continue

    // "State: city, city, city" form
    const colonIdx = line.indexOf(":")
    if (colonIdx > 0) {
      const head = line.slice(0, colonIdx).trim()
      const tail = line.slice(colonIdx + 1)
      const code = (validCodes.has(head.toUpperCase()) && head.toUpperCase() as StateCode)
        || labelToCode.get(head.toLowerCase())
      if (code) {
        for (const city of tail.split(",")) push(code, city)
        continue
      }
    }

    // "City, ST" form — last token after the final comma is the state code
    const lastComma = line.lastIndexOf(",")
    if (lastComma > 0) {
      const city = line.slice(0, lastComma).trim()
      const codeRaw = line.slice(lastComma + 1).trim().toUpperCase()
      if (validCodes.has(codeRaw)) {
        push(codeRaw as StateCode, city)
        continue
      }
    }

    // "City ST" form — last whitespace-delimited token is the state code
    const ws = line.lastIndexOf(" ")
    if (ws > 0) {
      const codeRaw = line.slice(ws + 1).trim().toUpperCase()
      if (validCodes.has(codeRaw)) {
        push(codeRaw as StateCode, line.slice(0, ws).trim())
      }
    }
  }
  return out
}

function mergeCities(defaults: string[], extras: string[] | undefined): string[] {
  if (!extras || extras.length === 0) return defaults
  const seen = new Set(defaults.map((c) => c.toLowerCase()))
  const merged = [...defaults]
  for (const c of extras) {
    if (!seen.has(c.toLowerCase())) {
      merged.push(c)
      seen.add(c.toLowerCase())
    }
  }
  return merged
}

/**
 * Build the list of search queries for a "State sweep" run across one or
 * more states. Template uses {city}, {state}, {stateLabel} placeholders.
 * `extraCitiesByState` supplements the STATE_CITIES preset for any state
 * the user typed extras for in the modal.
 */
export function buildStateSweepQueries(opts: {
  template: string
  states: StateCode[]
  extraCitiesByState?: Partial<Record<StateCode, string[]>>
}): string[] {
  const out: string[] = []
  for (const state of opts.states) {
    const cities = mergeCities(
      STATE_CITIES[state] ?? [],
      opts.extraCitiesByState?.[state],
    )
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
  extraCitiesByState?: Partial<Record<StateCode, string[]>>
}): string[] {
  const out: string[] = []
  for (const state of opts.states) {
    const cities = mergeCities(
      STATE_CITIES[state] ?? [],
      opts.extraCitiesByState?.[state],
    )
    for (const specialty of opts.specialties) {
      for (const city of cities) {
        out.push(`${specialty} in ${city}, ${state}`)
      }
    }
  }
  return out
}

export function totalCitiesForStates(
  states: StateCode[],
  extraCitiesByState?: Partial<Record<StateCode, string[]>>,
): number {
  return states.reduce((acc, s) => {
    const merged = mergeCities(STATE_CITIES[s] ?? [], extraCitiesByState?.[s])
    return acc + merged.length
  }, 0)
}
