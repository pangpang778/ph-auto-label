(function () {
  function parseRgb(color) {
    const match = String(color || '').match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    return match ? `rgb(${match[1]}, ${match[2]}, ${match[3]})` : '#36a3ff';
  }

  function Chart(canvas, config) {
    this.canvas = canvas;
    this.config = config || {};
    this.resizeObserver = null;
    this.draw = this.draw.bind(this);
    this.draw();
    if (window.ResizeObserver) {
      this.resizeObserver = new ResizeObserver(this.draw);
      this.resizeObserver.observe(canvas.parentElement || canvas);
    }
  }

  Chart.prototype.destroy = function () {
    if (this.resizeObserver) this.resizeObserver.disconnect();
  };

  Chart.prototype.draw = function () {
    const canvas = this.canvas;
    const ctx = canvas.getContext('2d');
    const rect = (canvas.parentElement || canvas).getBoundingClientRect();
    const width = Math.max(320, Math.floor(rect.width || 640));
    const height = Math.max(240, Math.floor(rect.height || 360));
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const datasets = this.config.data?.datasets || [];
    const labels = this.config.data?.labels || [];
    const values = datasets.flatMap(dataset => dataset.data || []).map(Number).filter(Number.isFinite);
    if (!datasets.length || !values.length) return;

    const padding = { top: 20, right: 20, bottom: 38, left: 52 };
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;

    ctx.strokeStyle = 'rgba(148,163,184,0.28)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padding.left, padding.top);
    ctx.lineTo(padding.left, padding.top + plotHeight);
    ctx.lineTo(padding.left + plotWidth, padding.top + plotHeight);
    ctx.stroke();

    ctx.fillStyle = '#c0cad8';
    ctx.font = '12px sans-serif';
    ctx.fillText(max.toFixed(3), 6, padding.top + 4);
    ctx.fillText(min.toFixed(3), 6, padding.top + plotHeight);

    datasets.forEach((dataset, datasetIndex) => {
      const points = (dataset.data || []).map((value, index) => {
        const x = padding.left + (labels.length <= 1 ? 0 : (index / (labels.length - 1)) * plotWidth);
        const y = padding.top + plotHeight - ((Number(value) - min) / range) * plotHeight;
        return { x, y };
      });
      if (!points.length) return;

      ctx.strokeStyle = parseRgb(dataset.borderColor);
      ctx.lineWidth = 2;
      ctx.beginPath();
      points.forEach((point, index) => {
        if (index === 0) ctx.moveTo(point.x, point.y);
        else ctx.lineTo(point.x, point.y);
      });
      ctx.stroke();

      const legendX = padding.left + (datasetIndex % 3) * 190;
      const legendY = 14 + Math.floor(datasetIndex / 3) * 16;
      ctx.fillStyle = parseRgb(dataset.borderColor);
      ctx.fillRect(legendX, legendY - 8, 10, 3);
      ctx.fillStyle = '#e2e8f0';
      ctx.fillText(dataset.label || `series ${datasetIndex + 1}`, legendX + 14, legendY);
    });
  };

  window.Chart = Chart;
}());
