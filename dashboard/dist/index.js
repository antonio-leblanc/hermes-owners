/* Native dashboard IIFE: React and authenticated requests come from the host. */
(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  const R = SDK.React, h = R.createElement;
  const { Button, Badge, Card } = SDK.components;
  const time = value => value ? new Date(value).toLocaleString() : "Unknown";
  // The native profile provider synchronizes selection to ?profile=. Plugin
  // endpoints are not automatically scoped by fetchJSON at this SDK revision.
  const scope = () => new URLSearchParams(window.location.search).get("profile") ??
    window.__HERMES_INITIAL_PROFILE__ ?? window.__HERMES_DASHBOARD_PROFILE__ ?? "";
  function List(props) {
    return props.items.length ? h("ul", null, props.items.map((item, i) => h("li", { key: i }, item))) :
      h("p", { className: "owners-muted" }, "Not declared");
  }
  function Bot(props) {
    const b = props.bot;
    return h("div", { className: "owners-bot" },
      h("div", { className: "owners-row" }, h("code", null, b.name),
        h(Badge, { variant: "outline" }, b.status)),
      h("small", { className: "owners-muted" }, b.availability + " · " + time(b.updated_at)));
  }
  // SDK 1.1.0 exposes no profile subscription (runtime ee8dd6c886): the
  // switcher only rewrites ?profile=, so read it on a short interval instead of
  // patching the host's history API.
  function useProfile() {
    const [profile, setProfile] = R.useState(scope);
    R.useEffect(() => {
      const id = setInterval(() => setProfile(scope()), 500);
      return () => clearInterval(id);
    }, []);
    return profile;
  }
  const handbacks = edge => {
    const known = edge.returned == null || (edge.returned === 0 && edge.handback_unknown === edge.total) ?
      "Historical handbacks unknown" : edge.returned + " historical handbacks observed";
    return known + (edge.handback_unknown ? " · " + edge.handback_unknown + " cards: handback history unknown" :
      edge.handback_unknown == null ? " · Remaining handback history unknown" : "");
  };
  function AreaGraph({ data, selected, select }) {
    const root = R.useRef(null);
    const marker = "owners-arrow-" + R.useId().replace(/[^a-zA-Z0-9_-]/g, "");
    const [layout, setLayout] = R.useState({ width: 0, height: 0, paths: [] });
    const edges = R.useMemo(() => [
      ...data.configured_routes.map(edge => ({ ...edge, observed: false })),
      ...(data.kanban.availability === "available" ? data.observed_handoffs.map(edge => ({ ...edge, observed: true })) : [])
    ], [data]);
    R.useLayoutEffect(() => {
      const node = root.current;
      let frame;
      const measure = () => {
        const box = node.getBoundingClientRect();
        const cards = new Map(Array.from(node.querySelectorAll("[data-area]")).map(card => {
          const r = card.getBoundingClientRect();
          return [card.dataset.area, { x: r.left - box.left, y: r.top - box.top, w: r.width, h: r.height }];
        }));
        const pairs = new Map();
        edges.forEach(edge => {
          const key = JSON.stringify([edge.from, edge.to].sort());
          pairs.set(key, (pairs.get(key) || 0) + 1);
        });
        const used = new Map();
        const paths = edges.flatMap((edge, i) => {
          const a = cards.get(edge.from), b = cards.get(edge.to);
          if (!a || !b) return []; // Undeclared endpoints remain in the text alternative.
          const key = JSON.stringify([edge.from, edge.to].sort());
          const lane = used.get(key) || 0;
          used.set(key, lane + 1);
          const offset = (lane - (pairs.get(key) - 1) / 2) * Math.min(14,
            Math.min(a.w, b.w, a.h, b.h) * .4 / Math.max(1, pairs.get(key) - 1));
          let d;
          if (edge.from !== edge.to && Math.abs(a.y - b.y) < 2) {
            const rightward = a.x < b.x;
            const sx = rightward ? a.x + a.w : a.x, tx = rightward ? b.x : b.x + b.w;
            const sy = a.y + a.h / 2 + offset, ty = b.y + b.h / 2 + offset;
            const mid = (sx + tx) / 2;
            d = `M ${sx} ${sy} C ${mid} ${sy}, ${mid} ${ty}, ${tx} ${ty}`;
          } else {
            const downward = a.y <= b.y;
            const sx = a.x + a.w * .65 + offset, tx = b.x + b.w * .35 + offset;
            const sy = downward ? a.y + a.h : a.y, ty = downward ? b.y : b.y + b.h;
            const direction = downward ? 1 : -1;
            const spacing = 1 / Math.max(1, edges.length);
            const startY = sy + direction * (16 + i * 32 * spacing);
            const endY = ty - direction * (16 + i * 32 * spacing);
            const rail = box.width - 12 - i * 64 * spacing;
            d = `M ${sx} ${sy} V ${startY} H ${rail} V ${endY} H ${tx} V ${ty}`;
          }
          return [{ ...edge, d, key: i }];
        });
        setLayout({ width: box.width, height: box.height, paths });
      };
      const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(measure); };
      const observer = new ResizeObserver(schedule);
      observer.observe(node);
      node.querySelectorAll("[data-area]").forEach(card => observer.observe(card));
      measure();
      return () => { observer.disconnect(); cancelAnimationFrame(frame); };
    }, [edges]);
    return h(R.Fragment, null,
      h("div", { className: "owners-legend", "aria-label": "Map legend" },
        h("span", null, h("i", { className: "owners-line owners-line-configured", "aria-hidden": true }), "Configured suggestion"),
        h("span", null, h("i", { className: "owners-line owners-line-observed", "aria-hidden": true }), "Observed handoff"),
        h("small", { className: "owners-muted" }, "Arrows point to the receiving area. Parallel routes retain both directions. Counts and history availability are listed below.")),
      h("div", { ref: root, className: "owners-graph", style: { "--owners-rail": "88px", "--owners-row-gap": "104px" } },
        h("svg", { className: "owners-graph-lines", width: layout.width, height: layout.height,
          viewBox: `0 0 ${layout.width || 1} ${layout.height || 1}`, "aria-hidden": true, focusable: "false" },
          h("defs", null, [false, true].map(observed => h("marker", { key: String(observed), id: marker + (observed ? "-observed" : "-configured"),
            viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto", markerUnits: "userSpaceOnUse" },
            h("path", { d: "M 0 0 L 10 5 L 0 10 Z", className: observed ? "owners-arrow-observed" : "owners-arrow-configured" })))),
          layout.paths.map(edge => h("path", { key: edge.key, d: edge.d,
            className: "owners-graph-edge " + (edge.observed ? "owners-graph-observed" : "owners-graph-configured"),
            markerEnd: `url(#${marker}${edge.observed ? "-observed" : "-configured"})` },
            h("title", null, edge.from + " → " + edge.to + (edge.observed ? ": " + edge.total + " observed · " + handbacks(edge) : ": configured suggestion"))))),
        h("div", { className: "owners-areas", role: "group", "aria-label": "Areas; route descriptions below" }, data.areas.map(a =>
          h("button", { key: a.id, type: "button", "data-area": a.id, className: "owners-area", "aria-pressed": a.id === selected,
            onClick: () => select(a.id), "aria-label": "Select area " + a.id },
            h("span", { className: "owners-eyebrow" }, "AREA"), h("span", { className: "owners-area-title" }, a.id),
            h("span", { className: "owners-area-responsibilities" }, a.owns.slice(0, 2).join(" · ") || "Responsibilities not declared"),
            h("span", { className: "owners-muted" }, a.profiles.length + " profile" + (a.profiles.length === 1 ? "" : "s") + " · " +
              (a.profiles.length ? a.profiles.map(b => b.status).join(", ") : "unknown")),
            h("small", { className: "owners-muted" }, a.profiles.map(b => b.availability).join(" · ") || "No bot declared"))))));
  }
  function Page() {
    const profile = useProfile();
    const [result, setResult] = R.useState(null);
    const [error, setError] = R.useState(null);
    const [loading, setLoading] = R.useState(true);
    const [selected, setSelected] = R.useState(null);
    const [refresh, setRefresh] = R.useState(0);

    R.useEffect(() => {
      let cancelled = false;
      setLoading(true); setError(null); setResult(null);
      SDK.fetchJSON("/api/plugins/owners/snapshot" + (profile ? "?profile=" + encodeURIComponent(profile) : ""))
        .then(data => {
          if (cancelled || scope() !== profile) return;
          setResult({ profile, data });
          setSelected(old => data.areas.some(a => a.id === old) ? old : (data.areas[0]?.id ?? null));
        })
        .catch(() => { if (!cancelled && scope() === profile) setError("Snapshot unavailable. Check that Owners is enabled in this profile and retry."); })
        .finally(() => { if (!cancelled && scope() === profile) setLoading(false); });
      return () => { cancelled = true; };
    }, [profile, refresh]);
    // Never paint a response from a previous URL scope, including before the
    // history listener has noticed the switch. Late requests are discarded above.
    const data = result && result.profile === profile && profile === scope() ? result.data : null;
    const area = data?.areas.find(a => a.id === selected);
    const select = id => setSelected(id);
    function Edge(props) {
      const edge = props.edge, observed = props.observed;
      return h("div", { className: "owners-edge " + (observed ? "owners-observed" : "owners-configured") },
        h("button", { type: "button", onClick: () => select(edge.from), "aria-label": "Select " + edge.from }, edge.from),
        h("span", { "aria-hidden": true }, "→"),
        h("button", { type: "button", onClick: () => select(edge.to), "aria-label": "Select " + edge.to }, edge.to),
        h("small", null, observed ? edge.total + " observed · " + handbacks(edge) : "Configured suggestion"));
    }
    return h("section", { className: "owners-dashboard", "aria-label": "Company areas dashboard" },
      h("header", { className: "owners-header" },
        h("div", null, h("p", { className: "owners-eyebrow" }, "OWNERS · READ ONLY"),
          h("h1", null, data?.company || "Company areas"),
          h("p", { className: "owners-muted" }, "Ownership first. Native state and handoff evidence, not a second workflow engine.")),
        h(Button, { variant: "outline", disabled: loading && profile === scope(), onClick: () => setRefresh(n => n + 1) }, "Refresh")),
      h("p", { className: "owners-muted owners-stamp" }, "Profile: " + (profile || "serving profile") +
        " · Snapshot: " + time(data?.generated_at) + " · Charter: " + time(data?.charter.updated_at)),
      error && h("div", { role: "alert", className: "owners-notice" }, error),
      !error && !data && h("p", { role: "status", "aria-live": "polite" }, "Loading areas…"),
      data && h(R.Fragment, null,
        data.warnings.length > 0 && h("div", { className: "owners-notice", role: "status" },
          h("strong", null, "Availability notes"), h(List, { items: data.warnings })),
        !data.areas.length ? h(Card, { className: "owners-empty" },
          h("h2", null, "No areas to display"), h("p", null, data.charter.availability === "available" ?
            "The charter has no departments. Declare responsibilities and a profile for each area." :
            "Configure a valid fleet charter in the native Owners plugin settings. Example data is never substituted.")) :
        h("div", { className: "owners-layout" },
          h("div", { className: "owners-map" },
            h("h2", null, "Company map"),
            h("p", { className: "owners-muted" }, "Select an area to inspect its boundaries and handoffs. Links are not a reporting hierarchy."),
            h(AreaGraph, { data, selected, select }),
            h("div", { className: "owners-links", "aria-label": "Connection legend and links" },
              h("h3", null, "Configured routes"),
              h("p", { className: "owners-muted" }, "Dashed links · charter suggestions, not enforced routing."),
              data.configured_routes.length ? data.configured_routes.map((e, i) => h(Edge, { edge: e, key: i })) :
                h("p", { className: "owners-muted" }, "No routes declared."),
              h("h3", null, "Observed handoffs"),
              h("p", { className: "owners-muted" }, "Solid links · Owners provenance on native cards in the scan window. Handback requires historical transfer evidence."),
              data.kanban.availability !== "available" ? h("p", null, "Observations unknown: Kanban unavailable.") :
                data.observed_handoffs.length ? data.observed_handoffs.map((e, i) => h(Edge, { edge: e, observed: true, key: i })) :
                  h("p", { className: "owners-muted" }, "No Owners handoffs observed in the scan window."),
              h("small", { className: "owners-muted" }, "Board: " + data.kanban.availability + " · Source: " + time(data.kanban.updated_at) +
                " · Scanned: " + (data.kanban.scanned_count ?? "unknown") + (data.kanban.truncated ? " (limited)" : "")))),
          area && h(Card, { className: "owners-detail", "aria-label": "Selected area details", "aria-live": "polite" },
            h("p", { className: "owners-eyebrow" }, "SELECTED AREA"), h("h2", null, area.id),
            h("h3", null, "Responsibilities"), h(List, { items: area.owns }),
            h("h3", null, "Does not own"), h(List, { items: area.does_not_own }),
            h("h3", null, "Bots and freshness"),
            area.profiles.length ? area.profiles.map(b => h(Bot, { key: b.name, bot: b })) : h("p", null, "No profile declared; status unknown."),
            h("h3", null, "Handoff summary"),
            data.observed_handoffs.filter(e => e.from === area.id || e.to === area.id).map((e, i) =>
              h("p", { key: i }, e.from + " → " + e.to + ": " + e.total + " cards · " + handbacks(e) + " · " +
                Object.entries(e.statuses).map(([s, n]) => n + " " + s).join(", "))),
            h("h3", null, "Related native cards"),
            h("p", { className: "owners-muted" }, "Latest 50 observed handoff cards. Titles, bodies, ticket references and event summaries are not exposed. Blocked does not automatically mean stalled."),
            data.kanban.availability !== "available" ? h("p", null, "Task state unknown.") :
              h("ul", { className: "owners-tasks" }, data.tasks.filter(t => t.from === area.id || t.to === area.id).map(t =>
                h("li", { key: t.id }, h("div", { className: "owners-row" }, h("code", null, t.id), h(Badge, { variant: "outline" }, t.status)),
                  h("p", null, t.from + " → " + t.to + " · Current area: " + (t.current_area || "unknown")),
                  h("small", null, "Handback: " + ({ observed: "observed", not_observed: "not observed", unknown: "unknown" }[t.handback] || "unknown") +
                    " · Created: " + time(t.created_at)),
                  t.returned_at && h("small", null, "Transfer evidence: " + time(t.returned_at))))),
            data.kanban.availability === "available" && !data.tasks.some(t => t.from === area.id || t.to === area.id) &&
              h("p", { className: "owners-muted" }, "No related cards in the detail window."))),
        h("footer", { className: "owners-muted owners-footer" }, "Read-only · One declared profile per area today (#10 pending) · No lifecycle policy selection (#16) · No adoption or cost metrics (#19)")));
  }
  window.__HERMES_PLUGINS__.register("owners", Page);
})();
