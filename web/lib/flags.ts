/**
 * Front-end feature flags.
 *
 * SHOW_BILLING — master switch for every cost / credit / billing surface in the
 * UI (the topbar credit pill, the admin Credits & Usage pages and their menu
 * links, the bulk-scan credit estimate, per-card cost badges, etc.).
 *
 * Set to `false` to hide all of it for demos. The backend billing logic is left
 * completely intact — this only controls what the user sees. Flip back to `true`
 * (or set NEXT_PUBLIC_SHOW_BILLING=true) after the demo to restore it.
 */
export const SHOW_BILLING =
  process.env.NEXT_PUBLIC_SHOW_BILLING === "true" ? true : false

/**
 * SHOW_INTEGRATIONS — controls the admin Integrations page and its menu link.
 *
 * That page is currently a non-functional mock (it shows a "Saved" toast but
 * does not persist credentials), so it's hidden for demos. Set to `true` (or
 * NEXT_PUBLIC_SHOW_INTEGRATIONS=true) once the integration wiring is real.
 */
export const SHOW_INTEGRATIONS =
  process.env.NEXT_PUBLIC_SHOW_INTEGRATIONS === "true" ? true : false
