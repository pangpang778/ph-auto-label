/* ============================================================
   Model Evaluation & Comparison - frontend logic
   Standalone page (/evaluation). Self-contained helpers (no
   shared utils.js loaded on this page). Conforms to the
   backend API + schema contract.
   ============================================================ */
(function () {
    'use strict';

    // ---- state ----
    var state = {
        models: [],            // registry models [{id,name,version,model_path,...}]
        records: [],           // all eval records (newest first)
        selectedIds: [],       // selected record ids for comparison
        split: 'val',          // 'val' | 'test' metric toggle
        bestMap: null,         // {map50:id, map50_95:id, precision:id, recall:id, f1:id, speed_ms:id}
        trendChart: null,
        prChart: null,
        pollTimer: null,
        activeLaunchId: null
    };

    var POLL_INTERVAL = 1500;
    var CHART_COLORS = ['0,122,255', '52,199,89', '251,191,36', '255,59,48', '167,139,250', '45,212,191'];

    // ---- helpers ----
    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (ch) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[ch];
        });
    }
    function chartColor(index, alpha) {
        return 'rgba(' + CHART_COLORS[index % CHART_COLORS.length] + ', ' + (alpha == null ? 1 : alpha) + ')';
    }
    function fmtNum(v, digits) {
        var n = Number(v);
        if (!isFinite(n)) return '-';
        return n.toFixed(digits == null ? 3 : digits);
    }
    function fmtTime(iso) {
        if (!iso) return '-';
        try {
            var d = new Date(iso);
            if (isNaN(d.getTime())) return escapeHtml(iso);
            var pad = function (x) { return x < 10 ? '0' + x : '' + x; };
            return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
                ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
        } catch (e) { return escapeHtml(iso); }
    }
    function statusText(s) {
        return ({ queued: '排队中', running: '评估中', completed: '完成', failed: '失败' }[s]) || s || '-';
    }
    function toast(msg) {
        var el = document.getElementById('evToast');
        if (!el) return;
        el.textContent = msg;
        el.classList.add('show');
        setTimeout(function () { el.classList.remove('show'); }, 2400);
    }
    function metricsOf(record) {
        if (!record) return null;
        return state.split === 'test' ? (record.test || record.val) : (record.val || record.test);
    }

    // ---- API ----
    function fetchJson(url, opts) {
        return fetch(url, opts).then(function (resp) {
            return resp.json().then(function (data) {
                return { ok: resp.ok, status: resp.status, data: data };
            });
        });
    }

    function loadModels() {
        var sel = document.getElementById('evModelSelect');
        if (!sel) return;
        fetchJson('/api/models/registry').then(function (r) {
            state.models = (r.data && r.data.models) || [];
            if (!state.models.length) {
                sel.innerHTML = '<option value="">-- 暂无模型 --</option>';
                return;
            }
            sel.innerHTML = state.models.map(function (m) {
                var label = m.name || m.version || m.id;
                if (m.version && m.name && m.name.indexOf(m.version) === -1) label = m.name + ' (' + m.version + ')';
                return '<option value="' + escapeHtml(m.id) + '">' + escapeHtml(label) + '</option>';
            }).join('');
        }).catch(function (e) {
            sel.innerHTML = '<option value="">-- 加载失败 --</option>';
            console.error('load models failed', e);
        });
    }

    function loadRecords() {
        return fetchJson('/api/evaluations').then(function (r) {
            state.records = (r.data && r.data.records) || [];
            renderRecordsList();
            renderPrSelect();
            refreshComparison();
        }).catch(function (e) {
            console.error('load records failed', e);
            var list = document.getElementById('evRecordsList');
            if (list) list.innerHTML = '<div class="ev-empty">加载评估记录失败</div>';
        });
    }

    // ---- launch / polling ----
    function startEvaluation() {
        var sel = document.getElementById('evModelSelect');
        var modelId = sel && sel.value;
        if (!modelId) { toast('请先选择一个模型'); return; }
        var btn = document.getElementById('evStartBtn');
        if (btn) btn.disabled = true;
        showLaunch('已提交评估请求...', 0, null);

        fetchJson('/api/models/' + encodeURIComponent(modelId) + '/evaluate', { method: 'POST' })
            .then(function (r) {
                if (r.status === 409) {
                    var msg = (r.data && r.data.error) || '训练中，暂无法评估';
                    showLaunch(msg, 0, 'error');
                    toast(msg);
                    if (btn) btn.disabled = false;
                    return;
                }
                if (!r.ok || !r.data || !r.data.id) {
                    var err = (r.data && r.data.error) || ('启动评估失败 (' + r.status + ')');
                    showLaunch(err, 0, 'error');
                    toast(err);
                    if (btn) btn.disabled = false;
                    return;
                }
                state.activeLaunchId = r.data.id;
                pollEval(r.data.id, function (progress, status) {
                    showLaunch(statusText(status) + '...', progress, null);
                }, function (record) {
                    if (record.status === 'failed') {
                        showLaunch('评估失败：' + (record.error || '未知错误'), 100, 'error');
                    } else {
                        showLaunch('评估完成: mAP@0.5=' + fmtNum((record.val || {}).map50) +
                            ', mAP@0.5:0.95=' + fmtNum((record.val || {}).map50_95), 100, 'success');
                        loadRecords();
                    }
                    if (btn) btn.disabled = false;
                    state.activeLaunchId = null;
                });
            })
            .catch(function (e) {
                showLaunch('网络错误: ' + (e && e.message), 0, 'error');
                if (btn) btn.disabled = false;
            });
    }

    function pollEval(recordId, onProgress, onDone) {
        if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
        var tick = function () {
            fetchJson('/api/evaluations/' + encodeURIComponent(recordId))
                .then(function (r) {
                    var rec = r.data;
                    if (!rec) { state.pollTimer = setTimeout(tick, POLL_INTERVAL); return; }
                    var progress = Math.max(0, Math.min(100, Number(rec.progress || 0)));
                    if (onProgress) onProgress(progress, rec.status);
                    if (rec.status === 'completed' || rec.status === 'failed') {
                        if (onDone) onDone(rec);
                        state.pollTimer = null;
                        return;
                    }
                    state.pollTimer = setTimeout(tick, POLL_INTERVAL);
                })
                .catch(function () { state.pollTimer = setTimeout(tick, POLL_INTERVAL * 2); });
        };
        state.pollTimer = setTimeout(tick, POLL_INTERVAL);
    }

    function showLaunch(text, progress, level) {
        var box = document.getElementById('evLaunchStatus');
        var txt = document.getElementById('evLaunchText');
        var bar = document.getElementById('evProgressBar');
        if (box) box.style.display = 'block';
        if (txt) {
            txt.textContent = text;
            txt.className = 'ev-status' + (level ? ' ' + level : '');
        }
        if (bar) bar.style.width = Math.max(0, Math.min(100, progress)) + '%';
    }

    // ---- records list (comparison selector) ----
    function renderRecordsList() {
        var list = document.getElementById('evRecordsList');
        if (!list) return;
        if (!state.records.length) {
            list.innerHTML = '<div class="ev-empty">暂无评估记录</div>';
            updateSelectionCount();
            return;
        }
        list.innerHTML = '';
        state.records.forEach(function (rec) {
            var m = rec.val || {};
            var checked = state.selectedIds.indexOf(rec.id) !== -1 ? 'checked' : '';
            var item = document.createElement('div');
            item.className = 'ev-record-item' + (checked ? ' selected' : '');
            item.innerHTML =
                '<input type="checkbox" data-id="' + escapeHtml(rec.id) + '" ' + checked + '>' +
                '<div style="flex:1;min-width:0;">' +
                    '<div class="ev-rec-name">' + escapeHtml(rec.model_name || rec.model_id || rec.id) + '</div>' +
                    '<div class="ev-rec-meta">' +
                        '<span><i class="far fa-clock"></i> ' + fmtTime(rec.started_at) + '</span>' +
                        '<span class="ev-badge ' + escapeHtml(rec.status || '') + '">' + escapeHtml(statusText(rec.status)) + '</span>' +
                        (m.map50 != null ? '<span>mAP@0.5: ' + fmtNum(m.map50) + '</span>' : '') +
                    '</div>' +
                '</div>';
            var cb = item.querySelector('input[type=checkbox]');
            cb.addEventListener('change', function () { toggleSelect(rec.id, cb.checked, item); });
            item.addEventListener('click', function (e) {
                if (e.target === cb) return;
                cb.checked = !cb.checked;
                toggleSelect(rec.id, cb.checked, item);
            });
            list.appendChild(item);
        });
        updateSelectionCount();
    }

    function toggleSelect(id, checked, item) {
        if (checked) {
            if (state.selectedIds.indexOf(id) === -1) state.selectedIds.push(id);
            if (item) item.classList.add('selected');
        } else {
            state.selectedIds = state.selectedIds.filter(function (x) { return x !== id; });
            if (item) item.classList.remove('selected');
        }
        updateSelectionCount();
        refreshComparison();
    }
    function updateSelectionCount() {
        var el = document.getElementById('evSelectionCount');
        if (el) el.textContent = '已选 ' + state.selectedIds.length + ' 条';
        var jsonBtn = document.getElementById('evExportJsonBtn');
        var csvBtn = document.getElementById('evExportCsvBtn');
        var disabled = state.selectedIds.length === 0;
        if (jsonBtn) jsonBtn.disabled = disabled;
        if (csvBtn) csvBtn.disabled = disabled;
    }

    // ---- PR record select ----
    function renderPrSelect() {
        var sel = document.getElementById('evPrRecordSelect');
        if (!sel) return;
        var prev = sel.value;
        sel.innerHTML = '<option value="">-- 选择评估记录 --</option>' +
            state.records.map(function (rec) {
                var label = (rec.model_name || rec.model_id || rec.id) + ' · ' + fmtTime(rec.started_at);
                return '<option value="' + escapeHtml(rec.id) + '">' + escapeHtml(label) + '</option>';
            }).join('');
        if (prev && state.records.some(function (r) { return r.id === prev; })) sel.value = prev;
    }

    // ---- comparison refresh ----
    function refreshComparison() {
        var selected = state.records.filter(function (r) { return state.selectedIds.indexOf(r.id) !== -1; });
        if (state.selectedIds.length >= 2) {
            fetchCompare(state.selectedIds).then(function () {
                renderCompareTable(selected);
                renderTrendChart(selected);
            });
        } else {
            state.bestMap = null;
            renderCompareTable(selected);
            renderTrendChart(selected);
        }
    }

    function fetchCompare(ids) {
        return fetchJson('/api/evaluations/compare?ids=' + ids.map(encodeURIComponent).join(','))
            .then(function (r) {
                state.bestMap = (r.data && r.data.best) || null;
            })
            .catch(function () { state.bestMap = null; });
    }

    // ---- comparison table ----
    function renderCompareTable(selected) {
        var wrap = document.getElementById('evCompareTable');
        if (!wrap) return;
        if (state.selectedIds.length < 2) {
            wrap.innerHTML = '<div class="ev-empty">请勾选至少 2 条评估记录</div>';
            return;
        }
        // For speed_ms the "best" record is the one with lowest value; the contract
        // best.speed_ms already encodes that id, so we treat it like the others.
        var best = state.bestMap || {};
        var headers = ['模型', 'mAP@0.5', 'mAP@0.5:0.95', '每类mAP', '精确率', '召回率', 'F1', '推理速度(ms)', 'FPS', '评估时间'];
        var rows = selected.map(function (rec) {
            var m = metricsOf(rec) || {};
            var perClass = m.per_class || [];
            var perClassCell = perClass.length
                ? '<span class="ev-perclass">' + perClass.length + ' 类 (点击展开)</span>' +
                  '<div class="ev-perclass-list">' + perClass.map(function (c) {
                      return '<div class="pc-row"><span>' + escapeHtml(c.class || '?') + '</span>' +
                          '<span>mAP50 ' + fmtNum(c.map50) + ' · 50:95 ' + fmtNum(c.map50_95) + '</span></div>';
                  }).join('') + '</div>'
                : '-';
            return '<tr>' +
                '<td>' + escapeHtml(rec.model_name || rec.model_id || rec.id) + '</td>' +
                bestCell('map50', rec.id, best.map50, m.map50, true) +
                bestCell('map50_95', rec.id, best.map50_95, m.map50_95, true) +
                '<td>' + perClassCell + '</td>' +
                bestCell('precision', rec.id, best.precision, m.precision, true) +
                bestCell('recall', rec.id, best.recall, m.recall, true) +
                bestCell('f1', rec.id, best.f1, m.f1, true) +
                bestCell('speed_ms', rec.id, best.speed_ms, m.speed_ms, false, 1) +
                bestCell('fps', rec.id, null, m.fps, false, 1) +
                '<td>' + escapeHtml(fmtTime(rec.started_at)) + '</td>' +
                '</tr>';
        }).join('');

        wrap.innerHTML =
            '<table class="ev-table"><thead><tr>' +
            headers.map(function (h) { return '<th>' + escapeHtml(h) + '</th>'; }).join('') +
            '</tr></thead><tbody>' + rows + '</tbody></table>';

        // wire per-class expansion
        wrap.querySelectorAll('.ev-perclass').forEach(function (el) {
            el.addEventListener('click', function (e) {
                e.stopPropagation();
                var list = el.nextElementSibling;
                if (list) list.classList.toggle('open');
            });
        });
    }

    // Render a metric cell; highlight if this record id is the "best" for the metric.
    // higherBetter=true -> higher value is better; false -> lower is better (speed_ms).
    function bestCell(metric, recId, bestIdForMetric, value, higherBetter, digits) {
        var isBest = bestIdForMetric && recId === bestIdForMetric;
        var cls = 'num' + (isBest ? ' best-cell' : '');
        var title = isBest ? ' title="最佳"' : '';
        return '<td class="' + cls + '"' + title + '>' + escapeHtml(fmtNum(value, digits == null ? 3 : digits)) + '</td>';
    }

    // ---- trend chart ----
    function renderTrendChart(selected) {
        var canvas = document.getElementById('evTrendChart');
        if (!canvas || typeof Chart === 'undefined') return;
        if (state.trendChart) { state.trendChart.destroy(); state.trendChart = null; }

        if (state.selectedIds.length < 2) {
            var ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            return;
        }
        // group by model_id, sort each group by started_at
        var groups = {};
        selected.forEach(function (rec) {
            var key = rec.model_id || rec.model_name || 'model';
            if (!groups[key]) groups[key] = { name: rec.model_name || key, records: [] };
            groups[key].records.push(rec);
        });
        var groupKeys = Object.keys(groups);
        groupKeys.forEach(function (k) {
            groups[k].records.sort(function (a, b) {
                return (a.started_at || '').localeCompare(b.started_at || '');
            });
        });

        // Build a sorted union of time labels across all groups
        var allTimes = [];
        selected.forEach(function (rec) { if (rec.started_at) allTimes.push(rec.started_at); });
        allTimes.sort();
        var labels = allTimes.map(function (t) { return fmtTime(t); });

        var datasets = [];
        groupKeys.forEach(function (k, gi) {
            var g = groups[k];
            ['map50', 'map50_95'].forEach(function (metric, mi) {
                var data = allTimes.map(function (t) {
                    // pick the record in this group matching this time (first match)
                    var rec = g.records.find(function (r) { return r.started_at === t; });
                    if (!rec) return null;
                    var m = metricsOf(rec) || {};
                    var v = Number(m[metric]);
                    return isFinite(v) ? v : null;
                });
                datasets.push({
                    label: g.name + ' ' + (metric === 'map50' ? 'mAP@0.5' : 'mAP@0.5:0.95'),
                    data: data,
                    borderColor: chartColor(gi * 2 + mi),
                    backgroundColor: chartColor(gi * 2 + mi, 0.14),
                    tension: 0.3,
                    fill: false,
                    spanGaps: true,
                    pointRadius: 3,
                    pointHoverRadius: 5
                });
            });
        });

        state.trendChart = new Chart(canvas, {
            type: 'line',
            data: { labels: labels, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { position: 'top', labels: { boxWidth: 12, font: { size: 12 } } },
                    tooltip: { callbacks: { label: function (c) { return c.dataset.label + ': ' + (c.parsed.y == null ? '-' : c.parsed.y.toFixed(3)); } } }
                },
                scales: {
                    x: { grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { font: { size: 11 }, maxRotation: 45, minRotation: 0 } },
                    y: { grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { font: { size: 11 }, callback: function (v) { return Number(v).toFixed(2); } }, suggestedMin: 0, suggestedMax: 1 }
                }
            }
        });
    }

    // ---- PR chart ----
    function renderPrChart(recordId) {
        var canvas = document.getElementById('evPrChart');
        if (!canvas || typeof Chart === 'undefined') return;
        if (state.prChart) { state.prChart.destroy(); state.prChart = null; }
        var ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        if (!recordId) return;

        var rec = state.records.find(function (r) { return r.id === recordId; });
        if (!rec) return;
        var m = metricsOf(rec) || {};
        var curve = m.pr_curve || [];
        if (!curve.length) {
            ctx.fillStyle = '#6e6e73';
            ctx.font = '13px -apple-system';
            ctx.textAlign = 'center';
            ctx.fillText('该记录无 PR 曲线数据', canvas.width / 2, canvas.height / 2);
            return;
        }
        // sort by recall ascending for a clean line
        var sorted = curve.slice().sort(function (a, b) { return (a[0] || 0) - (b[0] || 0); });
        var data = sorted.map(function (p) { return { x: Number(p[0]), y: Number(p[1]) }; });

        state.prChart = new Chart(canvas, {
            type: 'line',
            data: {
                datasets: [{
                    label: 'PR 曲线 (' + escapeHtml(rec.model_name || rec.id) + ')',
                    data: data,
                    borderColor: chartColor(0),
                    backgroundColor: chartColor(0, 0.12),
                    tension: 0.2,
                    fill: true,
                    pointRadius: 0,
                    pointHoverRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'top', labels: { boxWidth: 12, font: { size: 12 } } },
                    tooltip: { callbacks: { label: function (c) { return 'R=' + c.parsed.x.toFixed(3) + ' P=' + c.parsed.y.toFixed(3); } } }
                },
                scales: {
                    x: { type: 'linear', min: 0, max: 1, title: { display: true, text: 'Recall' }, grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { font: { size: 11 } } },
                    y: { min: 0, max: 1, title: { display: true, text: 'Precision' }, grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { font: { size: 11 } } }
                }
            }
        });
    }

    // ---- export ----
    function exportSelected(format) {
        if (!state.selectedIds.length) { toast('请先选择评估记录'); return; }
        state.selectedIds.forEach(function (id) {
            window.location.href = '/api/evaluations/' + encodeURIComponent(id) + '/export?format=' + encodeURIComponent(format);
        });
    }

    // ---- wire up ----
    function init() {
        var startBtn = document.getElementById('evStartBtn');
        if (startBtn) startBtn.addEventListener('click', startEvaluation);
        var refreshModels = document.getElementById('evRefreshModelsBtn');
        if (refreshModels) refreshModels.addEventListener('click', loadModels);
        var refreshRecords = document.getElementById('evRefreshRecordsBtn');
        if (refreshRecords) refreshRecords.addEventListener('click', loadRecords);

        // metric toggle val/test
        var toggle = document.getElementById('evMetricToggle');
        if (toggle) {
            toggle.addEventListener('click', function (e) {
                var btn = e.target.closest('button[data-split]');
                if (!btn) return;
                state.split = btn.getAttribute('data-split');
                toggle.querySelectorAll('button').forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                var selected = state.records.filter(function (r) { return state.selectedIds.indexOf(r.id) !== -1; });
                renderCompareTable(selected);
                renderTrendChart(selected);
                if (document.getElementById('evPrRecordSelect').value) renderPrChart(document.getElementById('evPrRecordSelect').value);
            });
        }

        // PR record select
        var prSelect = document.getElementById('evPrRecordSelect');
        if (prSelect) prSelect.addEventListener('change', function () { renderPrChart(prSelect.value); });

        // export
        var jsonBtn = document.getElementById('evExportJsonBtn');
        if (jsonBtn) jsonBtn.addEventListener('click', function () { exportSelected('json'); });
        var csvBtn = document.getElementById('evExportCsvBtn');
        if (csvBtn) csvBtn.addEventListener('click', function () { exportSelected('csv'); });

        loadModels();
        loadRecords();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
