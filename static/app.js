const form = document.querySelector("#planForm");
const fileInput = document.querySelector("#fileInput");
const fileLabel = document.querySelector("#fileLabel");
const statusBox = document.querySelector("#status");
const metrics = document.querySelector("#metrics");
const summaryTable = document.querySelector("#summaryTable");
const clusterTable = document.querySelector("#clusterTable");
const routesTable = document.querySelector("#routesTable");
const managerFilter = document.querySelector("#managerFilter");
const downloadLink = document.querySelector("#downloadLink");

const managerColors = ["#1f7a63", "#276fbf", "#b25d24", "#7b4fb0", "#bf3952", "#4e7896"];
let map = L.map("map", { preferCanvas: true, attributionControl: false }).setView([55.75, 37.62], 7);
let layers = L.layerGroup().addTo(map);
let layerControl = null;
let lastRoutes = [];

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "",
}).addTo(map);

fileInput.addEventListener("change", () => {
  fileLabel.textContent = fileInput.files[0]?.name || "Выберите CSV или Excel";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusBox.textContent = "Строю план...";
  statusBox.classList.remove("error");
  downloadLink.hidden = true;

  try {
    const formData = new FormData(form);
    if (!formData.get("n_managers")) {
      formData.delete("n_managers");
    }

    const response = await fetch("/api/plan", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "Не удалось построить план");
    }

    renderResult(data);
    statusBox.textContent = data.warnings?.length ? data.warnings.join(" ") : "План готов";
    downloadLink.hidden = false;
  } catch (error) {
    statusBox.textContent = formatError(error);
    statusBox.classList.add("error");
  }
});

managerFilter.addEventListener("change", () => {
  renderRoutesTable(lastRoutes);
});

function renderResult(data) {
  lastRoutes = data.routes || [];
  renderMetrics(data.totals);
  renderTable(summaryTable, data.summary, {
    manager: "Менеджер",
    visits: "Визиты",
    unique_points: "Точки",
    distance_km: "Пробег, км",
  });
  renderTable(clusterTable, data.cluster_summary, {
    cluster: "Кластер",
    points: "Точки",
    required_visits: "Нужно визитов",
    avg_visits: "Среднее",
  });
  fillManagerFilter(data.summary || []);
  renderRoutesTable(lastRoutes);
  renderMap(data.points || [], lastRoutes);
}

function renderMetrics(totals) {
  const managerSource = {
    csv: "Менеджеров из CSV",
    manual: "Менеджеров задано",
    default: "Менеджеров по умолчанию",
  }[totals.managers_source] || "Менеджеров";

  const items = [
    [managerSource, totals.managers],
    ["Уникальных точек", totals.points],
    ["Требуется посещений", totals.required_visits],
    ["Всего посещений", totals.planned_visits],
    ["Выполнение", `${totals.completion_percent}%`],
    ["Пробег", `${totals.distance_km} км`],
  ];

  metrics.innerHTML = items
    .map(([label, value]) => `<div class="metric"><strong>${value}</strong><span>${label}</span></div>`)
    .join("");
}

function fillManagerFilter(summary) {
  const current = managerFilter.value;
  managerFilter.innerHTML = '<option value="">Все менеджеры</option>';
  summary.forEach((row) => {
    const option = document.createElement("option");
    option.value = row.manager;
    option.textContent = `Менеджер ${row.manager}`;
    managerFilter.append(option);
  });
  managerFilter.value = current;
}

function renderRoutesTable(routes) {
  const manager = managerFilter.value;
  const filtered = manager === "" ? routes : routes.filter((row) => String(row.manager) === manager);
  renderTable(routesTable, filtered.slice(0, 500), {
    manager: "Менеджер",
    day: "День",
    route_order: "Порядок",
    point_id: "Точка",
    n_visits: "Нужно",
    cluster: "Кластер",
    distance_from_prev_km: "От прошлой, км",
  });
}

function renderTable(table, rows, columns) {
  const headers = Object.entries(columns);
  if (!rows || rows.length === 0) {
    table.innerHTML = "<tbody><tr><td>Нет данных</td></tr></tbody>";
    return;
  }

  table.innerHTML = `
    <thead><tr>${headers.map(([, label]) => `<th>${label}</th>`).join("")}</tr></thead>
    <tbody>
      ${rows
        .map(
          (row) =>
            `<tr>${headers.map(([key]) => `<td>${formatValue(row[key])}</td>`).join("")}</tr>`,
        )
        .join("")}
    </tbody>
  `;
}

function renderMap(points, routes) {
  map.removeLayer(layers);
  if (layerControl) {
    map.removeControl(layerControl);
  }

  layers = L.layerGroup().addTo(map);
  const overlayLayers = {};
  const bounds = [];
  const clusters = groupBy(points, (point) => point.cluster);

  clusters.forEach((clusterPoints, cluster) => {
    const color = managerColors[Number(cluster) % managerColors.length];
    const clusterLayer = L.layerGroup();
    const hull = convexHull(clusterPoints.map((point) => [point.lat, point.lon]));

    if (hull.length >= 3) {
      L.polygon(hull, {
        color,
        fillColor: color,
        fillOpacity: 0.18,
        weight: 3,
      })
        .bindTooltip(`Зона кластера ${cluster}`)
        .addTo(clusterLayer);
    }

    clusterLayer.addTo(layers);
    overlayLayers[`Зона кластера ${cluster}`] = clusterLayer;
  });

  const managerLayers = new Map();
  const managerIds = [...new Set(routes.map((route) => route.manager))].sort((a, b) => a - b);

  managerIds.forEach((manager) => {
    const managerLayer = L.layerGroup();
    managerLayer.addTo(layers);
    managerLayers.set(manager, managerLayer);
    overlayLayers[`Менеджер ${manager}`] = managerLayer;
  });

  points.forEach((point) => {
    const color = managerColors[point.cluster % managerColors.length];
    const pointManagers = uniqueManagersForPoint(routes, point.point_id);
    const targetLayer =
      pointManagers.length === 1 ? managerLayers.get(pointManagers[0]) || layers : layers;
    const marker = L.circleMarker([point.lat, point.lon], {
      radius: 5,
      color,
      weight: 2,
      fillColor: color,
      fillOpacity: point.visits_remain > 0 ? 0.35 : 0.8,
    }).bindPopup(
      `<b>${escapeHtml(point.point_id)}</b><br>
      Кластер: ${point.cluster}<br>
      Нужно визитов: ${point.n_visits}<br>
      Сделано: ${point.visits_done}<br>
      Осталось: ${point.visits_remain}`,
    );
    targetLayer.addLayer(marker);
    bounds.push([point.lat, point.lon]);
  });

  const grouped = new Map();
  routes.forEach((row) => {
    const key = `${row.manager}-${row.day}`;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(row);
  });

  grouped.forEach((rows) => {
    rows.sort((a, b) => a.route_order - b.route_order);
    if (rows.length < 2) return;
    const manager = rows[0].manager;
    const color = managerColors[manager % managerColors.length];
    const line = L.polyline(
      rows.map((row) => [row.lat, row.lon]),
      { color, weight: 2, opacity: 0.45 },
    ).bindTooltip(`Менеджер ${manager}, день ${rows[0].day}`);
    (managerLayers.get(manager) || layers).addLayer(line);
  });

  layerControl = L.control.layers(null, overlayLayers, { collapsed: false }).addTo(map);

  if (bounds.length) {
    map.fitBounds(bounds, { padding: [24, 24] });
  }
}

function groupBy(items, keyGetter) {
  const grouped = new Map();
  items.forEach((item) => {
    const key = keyGetter(item);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(item);
  });
  return grouped;
}

function uniqueManagersForPoint(routes, pointId) {
  return [...new Set(routes.filter((route) => route.point_id === pointId).map((route) => route.manager))];
}

function convexHull(latLonPoints) {
  const unique = [...new Map(latLonPoints.map(([lat, lon]) => [`${lat},${lon}`, [lat, lon]])).values()];
  if (unique.length <= 3) return unique;

  const sorted = unique.sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  const lower = [];
  for (const point of sorted) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], point) <= 0) {
      lower.pop();
    }
    lower.push(point);
  }

  const upper = [];
  for (let i = sorted.length - 1; i >= 0; i -= 1) {
    const point = sorted[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], point) <= 0) {
      upper.pop();
    }
    upper.push(point);
  }

  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

function cross(origin, a, b) {
  return (a[1] - origin[1]) * (b[0] - origin[0]) - (a[0] - origin[0]) * (b[1] - origin[1]);
}

function formatValue(value) {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.map(formatValue).join(", ");
  if (typeof value === "object") return escapeHtml(JSON.stringify(value));
  if (typeof value === "number") return Number.isInteger(value) ? value : value.toFixed(2);
  return escapeHtml(String(value));
}

function formatError(error) {
  if (typeof error?.message === "string" && error.message !== "[object Object]") {
    return error.message;
  }
  return "Не удалось построить план. Проверьте файл и параметры.";
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (char) => {
    const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
    return entities[char];
  });
}
