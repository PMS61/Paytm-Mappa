async function api(url, method = "GET", body) {
  const r = await fetch(url, {method, headers: {"Content-Type": "application/json"}, body: body && JSON.stringify(body)});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j));
  return j;
}
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const when = s => new Date(s).toLocaleString([], {dateStyle: "short", timeStyle: "short"});
