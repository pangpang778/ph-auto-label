function showTrainMetrics(jobId) {
    openTrainArtifactModal('训练指标曲线', '<div class="timeline-summary">指标加载中...</div>');
    fetchOptionalJson(`/api/train/jobs/${encodeURIComponent(jobId)}/metrics`)
        .then(data => renderTrainMetrics(data));
}

function renderTrainMetrics(data) {
    if (!data || data.unavailable || !Array.isArray(data.rows) || !data.rows.length) {
        setTrainArtifactBody('<div class="timeline-summary artifact-missing">metrics/results.csv 不存在或接口未就绪，可先查看 results.png。</div>');
        return;
    }

    const numericColumns = (data.columns || Object.keys(data.rows[0] || {})).filter(column => {
        if (column === 'epoch') return false;
        return data.rows.some(row => Number.isFinite(Number(row[column])));
    });
    const preferred = numericColumns.filter(column => /precision|recall|mAP|loss/i.test(column)).slice(0, 6);
    const chartColumns = preferred.length ? preferred : numericColumns.slice(0, 6);
    const tableHtml = renderMetricsTable(data.rows, chartColumns);

    if (typeof Chart === 'undefined' || !chartColumns.length) {
        setTrainArtifactBody(`<div class="timeline-summary artifact-missing">Chart.js 未加载，已显示表格结果。</div>${tableHtml}`);
        return;
    }

    setTrainArtifactBody(`<div class="train-chart-wrap"><canvas id="trainMetricsChart"></canvas></div>${tableHtml}`);
    const labels = data.rows.map((row, index) => row.epoch ?? row.Epoch ?? index + 1);
    const datasets = chartColumns.map((column, index) => ({
        label: column,
        data: data.rows.map(row => Number(row[column] || 0)),
        borderColor: chartColor(index),
        backgroundColor: chartColor(index, 0.18),
        tension: 0.25
    }));
    const canvas = document.getElementById('trainMetricsChart');
    if (!canvas) return;
    if (trainMetricsChart) trainMetricsChart.destroy();
    trainMetricsChart = new Chart(canvas, {
        type: 'line',
        data: { labels, datasets },
        options: { responsive: true, maintainAspectRatio: false }
    });
}

function renderMetricsTable(rows, columns) {
    const visibleRows = rows.slice(-20);
    const headers = ['epoch', ...columns].filter((column, index, all) => all.indexOf(column) === index);
    return `
        <table class="train-table metrics-table">
            <thead><tr>${headers.map(h => `<th>${escapeHtml(h)}</th>`).join('')}</tr></thead>
            <tbody>${visibleRows.map(row => `<tr>${headers.map(h => `<td>${escapeHtml(formatMetricValue(row[h]))}</td>`).join('')}</tr>`).join('')}</tbody>
        </table>
    `;
}
