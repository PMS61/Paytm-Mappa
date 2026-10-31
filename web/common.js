// KARMA // Shared Client Utilities
// Common API, formatting, and computational visualization helpers

async function api(url, method = "GET", body) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" }
  };
  if (body) {
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j);
    throw new Error(msg || `HTTP Error ${r.status}`);
  }
  return j;
}

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[c]));

const when = (s) => {
  if (!s) return "—";
  try {
    const d = new Date(s);
    return d.toLocaleString([], {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    });
  } catch {
    return String(s);
  }
};

const formatCurrency = (amt) => {
  if (amt === null || amt === undefined || amt === "") return "—";
  const num = Number(amt);
  if (isNaN(num)) return esc(amt);
  return "₹" + num.toLocaleString("en-IN");
};

function statusBadge(status) {
  const norm = String(status || "").toLowerCase();
  const label = norm.replace(/_/g, " ");
  return `<span class="badge status-${norm}">${esc(label)}</span>`;
}

function routeBadge(route) {
  const r = String(route || "").toUpperCase();
  if (r === "AI") {
    return `<span class="badge badge-ai">⚡ AI AUTOPILOT</span>`;
  }
  if (r === "HUMAN") {
    return `<span class="badge badge-human">👤 HUMAN QUEUE</span>`;
  }
  return `<span class="badge badge-outline">${esc(r || "UNASSIGNED")}</span>`;
}

function probMeter(title, pAi, lamThreshold = null) {
  const p = Math.max(0, Math.min(1, Number(pAi || 0)));
  const pct = (p * 100).toFixed(1);
  const markerStyle = lamThreshold !== null ? `left: ${Math.max(0, Math.min(100, lamThreshold * 100))}%;` : "display:none;";

  return `
    <div class="prob-meter-group">
      <div class="prob-meter-header">
        <span class="text-mono" style="font-weight:600;">${esc(title)}</span>
        <span class="text-mono" style="color:var(--color-ink); font-weight:700;">${pct}% AI</span>
      </div>
      <div class="prob-track">
        <div class="prob-fill-ai" style="width: ${pct}%;"></div>
        <div class="prob-marker" style="${markerStyle}" title="λ̂ Threshold: ${lamThreshold}"></div>
      </div>
      <div class="prob-meter-sub">
        <span>0.00 (Human)</span>
        ${lamThreshold !== null ? `<span>λ̂ = ${Number(lamThreshold).toFixed(3)}</span>` : ""}
        <span>1.00 (AI)</span>
      </div>
    </div>
  `;
}

function copyToClipboard(text, btnElement) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = btnElement.innerHTML;
    btnElement.innerHTML = "✓ COPIED";
    setTimeout(() => {
      btnElement.innerHTML = orig;
    }, 1500);
  });
}
