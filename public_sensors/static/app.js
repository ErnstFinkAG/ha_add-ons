(() => {
  "use strict";

  const canvas = document.getElementById("chart");
  const shell = document.getElementById("chart-shell");
  const tooltip = document.getElementById("tooltip");
  const titleEl = document.getElementById("page-title");
  const sensorValueEl = document.getElementById("sensor-current");
  const binaryValueEl = document.getElementById("binary-current");
  const binaryChipEl = document.getElementById("binary-chip");
  const updatedEl = document.getElementById("updated");
  const errorEl = document.getElementById("error");

  const COLORS = {
    background: "#111318",
    grid: "rgba(210, 215, 225, 0.28)",
    axis: "#c7ccd4",
    text: "#f3f5f7",
    sensor: "#e59217",
    binary: "#46a4e8",
    threshold: "#a6adb7",
    now: "#50c4ef"
  };

  let graphData = null;
  let refreshTimer = null;
  let latestLayout = null;

  function timeZone() {
    return (graphData && graphData.timezone) || "Europe/Zurich";
  }

  function formatValue(value, decimals) {
    const number = Number(value);
    if (value === null || value === undefined || Number.isNaN(number)) {
      return "—";
    }
    return number.toFixed(decimals);
  }

  function formatClock(timestamp) {
    return new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: timeZone()
    }).format(new Date(timestamp));
  }

  function formatDateTime(timestamp) {
    return new Intl.DateTimeFormat(undefined, {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      timeZone: timeZone()
    }).format(new Date(timestamp));
  }

  function sensorDecimals(data) {
    const unit = String(data.sensor.unit || "").toLowerCase();
    return unit === "mm" ? 1 : 2;
  }

  function setStatus(data) {
    titleEl.textContent = data.title || "Public Sensors";
    document.title = data.title || "Public Sensors";

    const decimals = sensorDecimals(data);
    const unit = data.sensor.unit ? " " + data.sensor.unit : "";
    sensorValueEl.textContent =
      data.sensor.name + ": " + formatValue(data.sensor.current, decimals) + unit;

    if (data.binary) {
      const state = String(data.binary.current || "unknown").toLowerCase();
      let display = state.toUpperCase();
      if (state === "on") display = "ON";
      if (state === "off") display = "OFF";

      binaryValueEl.textContent = data.binary.name + ": " + display;
      binaryChipEl.hidden = false;
      binaryChipEl.dataset.state = state;
    } else {
      binaryChipEl.hidden = true;
    }

    updatedEl.textContent = "Letztes Update: " + formatDateTime(Date.parse(data.updated));
  }

  function resizeCanvas() {
    const rect = shell.getBoundingClientRect();
    const width = Math.max(320, Math.floor(rect.width));
    const height = Math.max(320, Math.min(680, Math.floor(width * 0.48)));
    const ratio = Math.max(1, window.devicePixelRatio || 1);

    canvas.style.width = width + "px";
    canvas.style.height = height + "px";
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);

    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

    return { ctx: ctx, width: width, height: height };
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
    count = count || 5;
    const span = max - min;
    if (!Number.isFinite(span) || span <= 0) return [min];

    const raw = span / count;
    const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
    const residual = raw / magnitude;
    let niceResidual = 1;

    if (residual >= 5) niceResidual = 5;
    else if (residual >= 2) niceResidual = 2;

    const step = niceResidual * magnitude;
    const first = Math.ceil(min / step) * step;
    const ticks = [];

    for (let value = first; value <= max + step * 0.25; value += step) {
      ticks.push(value);
    }
    return ticks;
  }

  function drawGraph() {
    if (!graphData) return;

    const dimensions = resizeCanvas();
    const ctx = dimensions.ctx;
    const width = dimensions.width;
    const height = dimensions.height;
    const margin = {
      left: width < 520 ? 58 : 76,
      right: graphData.binary ? (width < 520 ? 54 : 74) : 28,
      top: 22,
      bottom: 62
    };

    const plot = {
      left: margin.left,
      right: width - margin.right,
      top: margin.top,
      bottom: height - margin.bottom
    };
    plot.width = plot.right - plot.left;
    plot.height = plot.bottom - plot.top;

    const xMin = Number(graphData.range.start);
    const xMax = Number(graphData.range.end);
    const yMin = Number(graphData.sensor.min);
    const yMax = Number(graphData.sensor.max);

    const xFor = function (timestamp) {
      return plot.left + ((timestamp - xMin) / (xMax - xMin)) * plot.width;
    };
    const yFor = function (value) {
      return plot.bottom - ((value - yMin) / (yMax - yMin)) * plot.height;
    };
    const pumpY = function (value) {
      return plot.bottom - (Number(value) / 1.2) * plot.height;
    };

    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = COLORS.background;
    ctx.fillRect(0, 0, width, height);

    ctx.save();
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 5]);

    const yTicks = niceTicks(yMin, yMax, 5);
    yTicks.forEach(function (tick) {
      const y = yFor(tick);
      ctx.beginPath();
      ctx.moveTo(plot.left, y);
      ctx.lineTo(plot.right, y);
      ctx.stroke();

      drawText(ctx, String(Math.round(tick)), plot.left - 12, y, {
        align: "right",
        baseline: "middle"
      });
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

    const leftLabel = graphData.sensor.unit
      ? graphData.sensor.name + " (" + graphData.sensor.unit + ")"
      : graphData.sensor.name;

    drawText(ctx, leftLabel, 18, plot.top + plot.height / 2, {
      align: "center",
      color: COLORS.axis,
      rotate: -Math.PI / 2
    });

    if (graphData.binary) {
      drawText(ctx, graphData.binary.name, width - 16, plot.top + plot.height / 2, {
        align: "center",
        color: COLORS.axis,
        rotate: Math.PI / 2
      });

      drawText(ctx, "ON", plot.right + 12, pumpY(1), {
        baseline: "middle"
      });
      drawText(ctx, "OFF", plot.right + 12, pumpY(0), {
        baseline: "middle"
      });
    }

    const threshold = Number(graphData.threshold && graphData.threshold.value);
    if (Number.isFinite(threshold) && threshold >= yMin && threshold <= yMax) {
      const y = yFor(threshold);
      ctx.save();
      ctx.strokeStyle = COLORS.threshold;
      ctx.lineWidth = 1;
      ctx.setLineDash([6, 5]);
      ctx.beginPath();
      ctx.moveTo(plot.left, y);
      ctx.lineTo(plot.right, y);
      ctx.stroke();
      ctx.restore();

      const label = graphData.threshold.label || String(threshold);
      ctx.save();
      ctx.font = "12px system-ui, sans-serif";
      const textWidth = ctx.measureText(label).width;
      const pad = 5;
      const boxX = plot.right - textWidth - pad * 2 - 5;
      const boxY = Math.max(plot.top + 2, y - 24);

      ctx.fillStyle = "rgba(24, 27, 32, 0.95)";
      ctx.fillRect(boxX, boxY, textWidth + pad * 2, 20);
      ctx.strokeStyle = COLORS.threshold;
      ctx.strokeRect(boxX, boxY, textWidth + pad * 2, 20);
      drawText(ctx, label, boxX + pad, boxY + 10, {
        color: COLORS.text,
        baseline: "middle"
      });
      ctx.restore();
    }

    const sensorData = graphData.sensor.data || [];
    if (sensorData.length > 0) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(plot.left, plot.top, plot.width, plot.height);
      ctx.clip();
      ctx.strokeStyle = COLORS.sensor;
      ctx.lineWidth = 1.35;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.beginPath();

      let started = false;
      sensorData.forEach(function (point) {
        const timestamp = Number(point[0]);
        const value = Number(point[1]);
        if (!Number.isFinite(timestamp) || !Number.isFinite(value)) return;

        const x = xFor(timestamp);
        const y = yFor(value);
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else {
          ctx.lineTo(x, y);
        }
      });

      ctx.stroke();
      ctx.restore();
    }

    if (graphData.binary && Array.isArray(graphData.binary.data)) {
      const binaryData = graphData.binary.data;
      ctx.save();
      ctx.beginPath();
      ctx.rect(plot.left, plot.top, plot.width, plot.height);
      ctx.clip();
      ctx.strokeStyle = COLORS.binary;
      ctx.lineWidth = 1.2;
      ctx.lineJoin = "miter";
      ctx.beginPath();

      if (binaryData.length > 0) {
        let previous = binaryData[0];
        ctx.moveTo(xFor(Number(previous[0])), pumpY(Number(previous[1])));

        for (let index = 1; index < binaryData.length; index += 1) {
          const current = binaryData[index];
          const x = xFor(Number(current[0]));
          ctx.lineTo(x, pumpY(Number(previous[1])));
          ctx.lineTo(x, pumpY(Number(current[1])));
          previous = current;
        }
      }

      ctx.stroke();
      ctx.restore();
    }

    ctx.save();
    ctx.strokeStyle = COLORS.now;
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 4]);
    ctx.beginPath();
    ctx.moveTo(plot.right, plot.top);
    ctx.lineTo(plot.right, plot.bottom);
    ctx.stroke();
    ctx.restore();

    ctx.save();
    ctx.strokeStyle = "rgba(210, 215, 225, 0.45)";
    ctx.lineWidth = 1;
    ctx.strokeRect(plot.left, plot.top, plot.width, plot.height);
    ctx.restore();

    const legendY = height - 20;
    let legendX = Math.max(plot.left, width / 2 - 150);

    ctx.fillStyle = COLORS.sensor;
    ctx.fillRect(legendX, legendY - 8, 14, 3);
    drawText(ctx, graphData.sensor.name, legendX + 20, legendY - 4, {
      color: COLORS.sensor,
      baseline: "middle"
    });
    legendX += 150;

    if (graphData.binary) {
      ctx.fillStyle = COLORS.binary;
      ctx.fillRect(legendX, legendY - 8, 14, 3);
      drawText(ctx, graphData.binary.name, legendX + 20, legendY - 4, {
        color: COLORS.binary,
        baseline: "middle"
      });
    }

    latestLayout = {
      plot: plot,
      xMin: xMin,
      xMax: xMax
    };
  }

  function nearestSensorPoint(timestamp) {
    const points = (graphData && graphData.sensor && graphData.sensor.data) || [];
    if (points.length === 0) return null;

    let low = 0;
    let high = points.length - 1;

    while (low < high) {
      const mid = Math.floor((low + high) / 2);
      if (Number(points[mid][0]) < timestamp) low = mid + 1;
      else high = mid;
    }

    const right = points[low];
    const left = points[Math.max(0, low - 1)];

    return Math.abs(Number(left[0]) - timestamp) <=
      Math.abs(Number(right[0]) - timestamp)
      ? left
      : right;
  }

  function binaryValueAt(timestamp) {
    const points =
      (graphData && graphData.binary && graphData.binary.data) || [];
    if (points.length === 0) return null;

    let value = Number(points[0][1]);
    for (const point of points) {
      if (Number(point[0]) > timestamp) break;
      value = Number(point[1]);
    }
    return value;
  }

  function hideTooltip() {
    tooltip.hidden = true;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function showTooltip(event) {
    if (!graphData || !latestLayout) {
      hideTooltip();
      return;
    }

    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const plot = latestLayout.plot;

    if (x < plot.left || x > plot.right) {
      hideTooltip();
      return;
    }

    const fraction = (x - plot.left) / plot.width;
    const timestamp =
      latestLayout.xMin +
      fraction * (latestLayout.xMax - latestLayout.xMin);
    const point = nearestSensorPoint(timestamp);

    if (!point) {
      hideTooltip();
      return;
    }

    const unit = graphData.sensor.unit ? " " + graphData.sensor.unit : "";
    const decimals = sensorDecimals(graphData);
    const binary = binaryValueAt(Number(point[0]));

    const rows = [
      "<strong>" + formatDateTime(Number(point[0])) + "</strong>",
      escapeHtml(graphData.sensor.name) + ": " +
        formatValue(point[1], decimals) + escapeHtml(unit)
    ];

    if (graphData.binary && binary !== null) {
      rows.push(
        escapeHtml(graphData.binary.name) + ": " +
        (binary >= 0.5 ? "ON" : "OFF")
      );
    }

    tooltip.innerHTML = rows.join("<br>");
    tooltip.hidden = false;

    const tipWidth = tooltip.offsetWidth;
    const tipHeight = tooltip.offsetHeight;
    const shellRect = shell.getBoundingClientRect();
    const desiredX = event.clientX - shellRect.left + 14;
    const desiredY = event.clientY - shellRect.top + 14;

    tooltip.style.left =
      Math.min(desiredX, shell.clientWidth - tipWidth - 8) + "px";
    tooltip.style.top =
      Math.min(desiredY, shell.clientHeight - tipHeight - 8) + "px";
  }

  async function fetchData() {
    try {
      const response = await fetch("./api/data", {
        cache: "no-store",
        headers: { Accept: "application/json" }
      });

      if (!response.ok) {
        const body = await response.json().catch(function () {
          return {};
        });
        throw new Error(body.error || "HTTP " + response.status);
      }

      const data = await response.json();
      graphData = data;
      setStatus(data);
      errorEl.hidden = true;
      drawGraph();

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

  let resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(drawGraph, 100);
  });

  canvas.addEventListener("mousemove", showTooltip);
  canvas.addEventListener("mouseleave", hideTooltip);

  fetchData();
})();
