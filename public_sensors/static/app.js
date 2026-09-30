(() => {
  "use strict";

  const canvas = document.getElementById("chart");
  const shell = document.getElementById("chart-shell");
  const tooltip = document.getElementById("tooltip");
  const titleEl = document.getElementById("page-title");
  const statusRow = document.getElementById("status-row");
  const entityPanel = document.getElementById("entity-panel");
  const updatedEl = document.getElementById("updated");
  const errorEl = document.getElementById("error");

  const COLORS = {
    background: "#111318",
    grid: "rgba(210, 215, 225, 0.24)",
    axis: "#c7ccd4",
    text: "#f3f5f7",
    threshold: "#a6adb7",
    now: "#50c4ef"
  };
  const SERIES_COLORS = [
    "#e59217", "#46a4e8", "#66c56c", "#d76ad8",
    "#f0c849", "#e56a6a", "#7c8cff", "#70d3c5"
  ];

  let pageData = null;
  let refreshTimer = null;
  let resizeTimer = null;
  let layout = null;

  function timeZone() {
    return (pageData && pageData.timezone) || "Europe/Zurich";
  }

  function formatDateTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat(undefined, {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      timeZone: timeZone()
    }).format(date);
  }

  function formatClock(timestamp) {
    return new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: timeZone()
    }).format(new Date(timestamp));
  }

  function clearElement(element) {
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function addChip(text, state) {
    const chip = document.createElement("span");
    chip.className = "status-chip";
    chip.textContent = text;
    if (state) chip.dataset.state = state;
    statusRow.appendChild(chip);
  }

  function displayValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function renderEntityBlock(entity, heading) {
    const attrs = entity && entity.attributes && typeof entity.attributes === "object"
      ? entity.attributes
      : {};
    const unit = attrs.unit_of_measurement ? " " + attrs.unit_of_measurement : "";

    const block = document.createElement("section");
    block.className = "entity-block";

    const head = document.createElement("div");
    head.className = "entity-block-head";

    const h2 = document.createElement("h2");
    h2.textContent = heading || attrs.friendly_name || entity.entity_id || "Sensor";
    head.appendChild(h2);

    const state = document.createElement("div");
    state.className = "entity-state";
    state.textContent = displayValue(entity.state) + unit;
    head.appendChild(state);
    block.appendChild(head);

    const table = document.createElement("dl");
    table.className = "attribute-grid";

    Object.keys(attrs).sort().forEach(function (key) {
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = displayValue(attrs[key]);
      table.appendChild(dt);
      table.appendChild(dd);
    });

    if (entity.last_updated) {
      const dt = document.createElement("dt");
      dt.textContent = "last_updated";
      const dd = document.createElement("dd");
      dd.textContent = formatDateTime(entity.last_updated);
      table.appendChild(dt);
      table.appendChild(dd);
    }

    block.appendChild(table);
    entityPanel.appendChild(block);
  }

  function renderEntityMode(data) {
    clearElement(statusRow);
    clearElement(entityPanel);
    shell.hidden = true;
    entityPanel.hidden = false;

    const entity = data.entity || {};
    const attrs = entity.attributes || {};
    const unit = attrs.unit_of_measurement ? " " + attrs.unit_of_measurement : "";
    addChip(
      (attrs.friendly_name || data.name) + ": " +
      displayValue(entity.state) + unit
    );
    renderEntityBlock(entity, data.name);
  }

  function renderYamlSummary(data) {
    clearElement(statusRow);
    clearElement(entityPanel);

    const chart = data.chart || {};
    const series = Array.isArray(chart.series) ? chart.series : [];

    series.forEach(function (item) {
      const unit = item.unit ? " " + item.unit : "";
      addChip(
        item.name + ": " + displayValue(item.current) + unit,
        String(item.current || "").toLowerCase()
      );
    });

    if (series.length > 0) {
      shell.hidden = false;
      entityPanel.hidden = true;
      drawGraph();
      return;
    }

    shell.hidden = true;
    entityPanel.hidden = false;
    const entities = Array.isArray(data.entities) ? data.entities : [];
    entities.forEach(function (entity) {
      renderEntityBlock(
        entity,
        entity.attributes && entity.attributes.friendly_name
      );
    });
  }

  function resizeCanvas() {
    const rect = shell.getBoundingClientRect();
    const width = Math.max(320, Math.floor(rect.width));
    const height = Math.max(340, Math.min(720, Math.floor(width * 0.5)));
    const ratio = Math.max(1, window.devicePixelRatio || 1);

    canvas.style.width = width + "px";
    canvas.style.height = height + "px";
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);

    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    return { ctx, width, height };
  }

  function drawText(ctx, text, x, y, options) {
    options = options || {};
    ctx.save();
    ctx.fillStyle = options.color || COLORS.axis;
    ctx.font = options.font || "12px system-ui, sans-serif";
    ctx.textAlign = options.align || "left";
    ctx.textBaseline = options.baseline || "alphabetic";
    if (options.rotate) {
      ctx.translate(x, y);
      ctx.rotate(options.rotate);
      ctx.fillText(text, 0, 0);
    } else {
      ctx.fillText(text, x, y);
    }
    ctx.restore();
  }

  function niceTicks(min, max, count) {
    const span = max - min;
    if (!Number.isFinite(span) || span <= 0) return [min];

    const raw = span / (count || 5);
    const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
    const residual = raw / magnitude;
    const niceResidual = residual >= 5 ? 5 : residual >= 2 ? 2 : 1;
    const step = niceResidual * magnitude;
    const first = Math.ceil(min / step) * step;
    const ticks = [];

    for (let value = first; value <= max + step * 0.25; value += step) {
      ticks.push(value);
    }
    return ticks;
  }

  function axisRanges(axes, series) {
    const result = {};

    axes.forEach(function (axis) {
      const values = [];
      series.forEach(function (item) {
        if (item.axis_id !== axis.id || !Array.isArray(item.data)) return;
        item.data.forEach(function (point) {
          const value = Number(point[1]);
          if (Number.isFinite(value)) values.push(value);
        });
      });

      let min = Number(axis.min);
      let max = Number(axis.max);
      if (!Number.isFinite(min)) min = values.length ? Math.min(...values) : 0;
      if (!Number.isFinite(max)) max = values.length ? Math.max(...values) : 1;
      if (max <= min) max = min + 1;

      if (
        axis.min === null || axis.min === undefined ||
        axis.max === null || axis.max === undefined
      ) {
        const pad = (max - min) * 0.05 || 1;
        if (axis.min === null || axis.min === undefined) min -= pad;
        if (axis.max === null || axis.max === undefined) max += pad;
      }

      result[axis.id] = { min, max };
    });

    return result;
  }

  function drawGraph() {
    if (!pageData || pageData.mode !== "yaml") return;

    const chart = pageData.chart || {};
    const series = Array.isArray(chart.series) ? chart.series : [];
    if (!series.length) return;

    const axes = Array.isArray(chart.axes) && chart.axes.length
      ? chart.axes
      : [{
          id: "default",
          min: null,
          max: null,
          opposite: false,
          title: ""
        }];

    const ranges = axisRanges(axes, series);
    const leftAxis = axes.find((axis) => !axis.opposite) || axes[0];
    const rightAxis = axes.find((axis) => axis.opposite) || null;

    const dimensions = resizeCanvas();
    const ctx = dimensions.ctx;
    const width = dimensions.width;
    const height = dimensions.height;
    const margin = {
      left: width < 520 ? 58 : 76,
      right: rightAxis ? (width < 520 ? 58 : 76) : 28,
      top: 22,
      bottom: 70
    };
    const plot = {
      left: margin.left,
      right: width - margin.right,
      top: margin.top,
      bottom: height - margin.bottom
    };
    plot.width = plot.right - plot.left;
    plot.height = plot.bottom - plot.top;

    const xMin = Number(pageData.range && pageData.range.start);
    const xMax = Number(pageData.range && pageData.range.end);

    const xFor = (timestamp) =>
      plot.left + ((timestamp - xMin) / (xMax - xMin)) * plot.width;

    const yFor = (axisId, value) => {
      const range = ranges[axisId] || ranges[leftAxis.id];
      return plot.bottom -
        ((value - range.min) / (range.max - range.min)) * plot.height;
    };

    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = COLORS.background;
    ctx.fillRect(0, 0, width, height);

    ctx.save();
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 5]);

    niceTicks(
      ranges[leftAxis.id].min,
      ranges[leftAxis.id].max,
      5
    ).forEach(function (tick) {
      const y = yFor(leftAxis.id, tick);
      ctx.beginPath();
      ctx.moveTo(plot.left, y);
      ctx.lineTo(plot.right, y);
      ctx.stroke();
      drawText(
        ctx,
        String(Math.round(tick * 100) / 100),
        plot.left - 10,
        y,
        { align: "right", baseline: "middle" }
      );
    });

    const xTickCount = width < 850 ? 5 : 8;
    for (let index = 0; index <= xTickCount; index += 1) {
      const fraction = index / xTickCount;
      const x = plot.left + fraction * plot.width;
      const timestamp = xMin + fraction * (xMax - xMin);
      ctx.beginPath();
      ctx.moveTo(x, plot.top);
      ctx.lineTo(x, plot.bottom);
      ctx.stroke();
      drawText(ctx, formatClock(timestamp), x, plot.bottom + 24, {
        align: "center"
      });
    }
    ctx.restore();

    if (leftAxis.title) {
      drawText(ctx, leftAxis.title, 18, plot.top + plot.height / 2, {
        align: "center",
        rotate: -Math.PI / 2
      });
    }

    if (rightAxis) {
      niceTicks(
        ranges[rightAxis.id].min,
        ranges[rightAxis.id].max,
        4
      ).forEach(function (tick) {
        drawText(
          ctx,
          String(Math.round(tick * 100) / 100),
          plot.right + 10,
          yFor(rightAxis.id, tick),
          { baseline: "middle" }
        );
      });

      if (rightAxis.title) {
        drawText(
          ctx,
          rightAxis.title,
          width - 16,
          plot.top + plot.height / 2,
          { align: "center", rotate: Math.PI / 2 }
        );
      }
    }

    const annotations = Array.isArray(chart.annotations)
      ? chart.annotations
      : [];

    annotations.forEach(function (annotation) {
      const axis = axes[Number(annotation.axis_index) || 0] || leftAxis;
      const range = ranges[axis.id];
      const value = Number(annotation.value);

      if (
        !range ||
        !Number.isFinite(value) ||
        value < range.min ||
        value > range.max
      ) return;

      const y = yFor(axis.id, value);
      ctx.save();
      ctx.strokeStyle = COLORS.threshold;
      ctx.setLineDash([6, 5]);
      ctx.beginPath();
      ctx.moveTo(plot.left, y);
      ctx.lineTo(plot.right, y);
      ctx.stroke();
      ctx.restore();

      drawText(
        ctx,
        annotation.label || String(value),
        plot.right - 6,
        y - 6,
        { align: "right", color: COLORS.text }
      );
    });

    series.forEach(function (item, seriesIndex) {
      const points = Array.isArray(item.data) ? item.data : [];
      if (!points.length) return;

      const color = SERIES_COLORS[seriesIndex % SERIES_COLORS.length];
      const step =
        item.binary ||
        String(item.curve).toLowerCase() === "stepline";
      const axisId = item.axis_id || leftAxis.id;

      ctx.save();
      ctx.beginPath();
      ctx.rect(plot.left, plot.top, plot.width, plot.height);
      ctx.clip();
      ctx.strokeStyle = color;
      ctx.lineWidth = Math.max(1, Number(item.stroke_width) || 2);
      ctx.lineJoin = step ? "miter" : "round";
      ctx.beginPath();

      let previous = points[0];
      ctx.moveTo(
        xFor(Number(previous[0])),
        yFor(axisId, Number(previous[1]))
      );

      for (let index = 1; index < points.length; index += 1) {
        const current = points[index];
        const x = xFor(Number(current[0]));
        if (step) {
          ctx.lineTo(x, yFor(axisId, Number(previous[1])));
          ctx.lineTo(x, yFor(axisId, Number(current[1])));
        } else {
          ctx.lineTo(x, yFor(axisId, Number(current[1])));
        }
        previous = current;
      }

      ctx.stroke();
      ctx.restore();
    });

    if (chart.show_now) {
      ctx.save();
      ctx.strokeStyle = COLORS.now;
      ctx.setLineDash([2, 4]);
      ctx.beginPath();
      ctx.moveTo(plot.right, plot.top);
      ctx.lineTo(plot.right, plot.bottom);
      ctx.stroke();
      ctx.restore();
    }

    ctx.strokeStyle = "rgba(210, 215, 225, 0.45)";
    ctx.strokeRect(plot.left, plot.top, plot.width, plot.height);

    let legendX = plot.left;
    const legendY = height - 18;

    series.forEach(function (item, index) {
      const color = SERIES_COLORS[index % SERIES_COLORS.length];
      ctx.fillStyle = color;
      ctx.fillRect(legendX, legendY - 7, 14, 3);
      drawText(ctx, item.name, legendX + 20, legendY - 3, {
        color,
        baseline: "middle"
      });
      legendX += Math.min(
        220,
        45 + String(item.name).length * 8
      );
    });

    layout = { plot, xMin, xMax, series };
  }

  function seriesValueAt(item, timestamp) {
    const points = Array.isArray(item.data) ? item.data : [];
    if (!points.length) return null;

    let best = points[0];
    for (const point of points) {
      if (Number(point[0]) > timestamp) break;
      best = point;
    }
    return best;
  }

  function hideTooltip() {
    tooltip.hidden = true;
  }

  function showTooltip(event) {
    if (!layout || !pageData || pageData.mode !== "yaml") {
      return hideTooltip();
    }

    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;

    if (x < layout.plot.left || x > layout.plot.right) {
      return hideTooltip();
    }

    const fraction = (x - layout.plot.left) / layout.plot.width;
    const timestamp =
      layout.xMin + fraction * (layout.xMax - layout.xMin);

    clearElement(tooltip);

    const strong = document.createElement("strong");
    strong.textContent = formatDateTime(timestamp);
    tooltip.appendChild(strong);

    layout.series.forEach(function (item) {
      const point = seriesValueAt(item, timestamp);
      if (!point) return;

      const line = document.createElement("div");
      const value = item.binary
        ? (Number(point[1]) >= 0.5 ? "ON" : "OFF")
        : String(point[1]);

      line.textContent =
        item.name + ": " + value +
        (item.unit ? " " + item.unit : "");
      tooltip.appendChild(line);
    });

    tooltip.hidden = false;

    const shellRect = shell.getBoundingClientRect();
    const desiredX = event.clientX - shellRect.left + 14;
    const desiredY = event.clientY - shellRect.top + 14;

    tooltip.style.left =
      Math.min(
        desiredX,
        shell.clientWidth - tooltip.offsetWidth - 8
      ) + "px";
    tooltip.style.top =
      Math.min(
        desiredY,
        shell.clientHeight - tooltip.offsetHeight - 8
      ) + "px";
  }

  function render(data) {
    pageData = data;
    const title = data.title || data.name || "Public Sensors";

    titleEl.textContent = title;
    document.title = title;
    updatedEl.textContent =
      "Letztes Update: " + formatDateTime(data.updated);

    if (data.mode === "entity") {
      renderEntityMode(data);
    } else {
      renderYamlSummary(data);
    }
  }

  async function fetchData() {
    try {
      const response = await fetch("data", {
        cache: "no-store",
        headers: { Accept: "application/json" }
      });

      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || "HTTP " + response.status);
      }

      const data = await response.json();
      errorEl.hidden = true;
      render(data);

      const interval =
        Math.max(15, Number(data.refresh_seconds || 60)) * 1000;

      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(fetchData, interval);
    } catch (error) {
      errorEl.textContent =
        "Daten konnten nicht geladen werden: " + error.message;
      errorEl.hidden = false;

      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(fetchData, 15000);
    }
  }

  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(drawGraph, 100);
  });

  canvas.addEventListener("mousemove", showTooltip);
  canvas.addEventListener("mouseleave", hideTooltip);

  fetchData();
})();
