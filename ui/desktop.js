import { host, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, STATUSBAR_AREAS } from '@hermes/plugin-sdk'
import * as React from 'react'

const ID = 'hermes-mail-gate'
const ROUTE = '/mail-gate'

/* @app */

function StatusCount({ ctx }) {
  const [count, setCount] = React.useState(0)
  React.useEffect(() => {
    let alive = true
    const load = () => ctx.rest('/summary').then(r => alive && setCount(r.pending_senders)).catch(() => {})
    load()
    const timer = setInterval(load, 30000)
    return () => { alive = false; clearInterval(timer) }
  }, [])
  if (!count) return null
  return React.createElement('button', {
    type: 'button', onClick: () => host.navigate(ROUTE), title: 'Senders waiting for your decision in Mail Gate',
    style: { display: 'inline-flex', alignItems: 'center', height: '100%', padding: '0 6px', fontSize: 11, border: 'none',
      background: 'none', cursor: 'pointer', color: 'var(--ui-orange, #e08a1e)', fontVariantNumeric: 'tabular-nums' }
  }, `✉ ${count} pending`)
}

export default {
  id: ID,
  name: 'Mail Gate',
  description: 'Allow, block and trust rules for the bots\' mailboxes, senders waiting for a decision, and gate activity.',
  register(ctx) {
    const api = {
      get: path => ctx.rest(path),
      post: (path, body) => ctx.rest(path, { method: 'POST', body })
    }
    const App = createMailGate(React, api)
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => React.createElement(App) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: ROUTE, label: 'Mail Gate', codicon: 'mail' } },
      { id: 'open', area: PALETTE_AREA, data: { id: `${ID}.open`, label: 'Open Mail Gate', run: () => host.navigate(ROUTE) } },
      { id: 'count', area: STATUSBAR_AREAS.right, order: 120, render: () => React.createElement(StatusCount, { ctx }) }
    ])

    let known = null
    const poll = () => ctx.rest('/summary').then(summary => {
      const keys = new Set(summary.pending_keys || [])
      if (known) {
        const fresh = [...keys].filter(key => !known.has(key))
        if (fresh.length) {
          const [bot, sender] = fresh[0].split('|')
          ctx.os.notify({
            title: 'Mail Gate: new sender waiting',
            body: fresh.length === 1 ? `${sender} wrote to ${bot}. Allow or block it.` : `${fresh.length} new senders are waiting for your decision.`,
            activate: ROUTE
          })
        }
      }
      known = keys
    }).catch(() => {})
    poll()
    ctx.setInterval(poll, 60000)
  }
}
