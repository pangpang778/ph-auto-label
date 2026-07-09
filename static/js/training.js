function showTrainingCenterModal() {
    const modal = document.getElementById('trainCenterModal');
    if (!modal) return;
    modal.style.display = 'block';
    loadTrainingCenter();
}

function setupTrainingCenterEvents() {
    const refreshBtn = document.getElementById('refreshTrainCenterBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', loadTrainingCenter);

    const initialBtn = document.getElementById('startInitialTrainBtn');
    if (initialBtn) initialBtn.addEventListener('click', () => startTraining('initial'));

    const incrementalBtn = document.getElementById('startIncrementalTrainBtn');
    if (incrementalBtn) incrementalBtn.addEventListener('click', () => startTraining('incremental'));

    const saveSplitBtn = document.getElementById('saveTrainSplitBtn');
    if (saveSplitBtn) saveSplitBtn.addEventListener('click', saveTrainSplit);

    const resetSplitBtn = document.getElementById('resetTrainSplitBtn');
    if (resetSplitBtn) resetSplitBtn.addEventListener('click', resetTrainSplit);

    const modal = document.getElementById('trainCenterModal');
    if (modal) {
        modal.addEventListener('click', function(e) {
            if (e.target === modal) stopTrainPolling();
        });
        const closeBtn = modal.querySelector('.close');
        if (closeBtn) closeBtn.addEventListener('click', stopTrainPolling);
    }
}

function loadTrainingCenter() {
    Promise.all([
        fetch('/api/train/readiness').then(r => r.json()),
        fetch('/api/train/jobs').then(r => r.json()),
        fetch('/api/models/registry').then(r => r.json()),
        fetch('/api/models/active').then(r => r.json()),
        fetchOptionalJson('/api/train/split')
    ])
        .then(([readiness, jobsData, modelsData, activeModel, splitData]) => {
            renderTrainReadiness(readiness);
            renderTrainSplit(splitData);
            renderTrainJobs(jobsData.jobs || []);
            renderModelRegistry(modelsData.models || [], activeModel);
            renderActiveModel(activeModel);
            syncAiModelWithActive(activeModel);
            startTrainPollingIfNeeded(jobsData.jobs || []);
        })
        .catch(error => {
            console.error('加载训练中心失败:', error);
            showToast('加载训练中心失败: ' + error.message);
        });
}

function renderTrainSplit(splitData) {
    const summary = document.getElementById('trainSplitSummary');
    const classFilters = document.getElementById('splitClassFilters');
    if (!summary || !classFilters) return;

    if (!splitData || splitData.unavailable) {
        trainSplitState = null;
        summary.innerHTML = '<span class="artifact-missing">切分接口未就绪，将使用后端默认切分。</span>';
        renderSplitClassOptions(classes.map(cls => cls.name), []);
        return;
    }

    trainSplitState = splitData;
    const config = splitData.config || splitData.split_config || splitData;
    const trainRatio = Number(config.train_ratio ?? config.train ?? 0.8);
    const valRatio = Number(config.val_ratio ?? config.val ?? 0.15);
    const testRatio = Number(config.test_ratio ?? config.test ?? 0.05);
    document.getElementById('splitTrainRatio').value = Number.isFinite(trainRatio) ? trainRatio : 0.8;
    document.getElementById('splitValRatio').value = Number.isFinite(valRatio) ? valRatio : 0.15;
    document.getElementById('splitTestRatio').value = Number.isFinite(testRatio) ? testRatio : 0.05;
    document.getElementById('splitSampleFilter').value = config.sample_filter || 'annotated';

    const classOptions = splitData.class_options || splitData.classes || classes.map(cls => cls.name);
    const selectedClasses = config.class_filter || splitData.class_filter || [];
    renderSplitClassOptions(classOptions, selectedClasses);
    summary.innerHTML = buildSplitSummaryHtml(splitData);
}

function renderSplitClassOptions(classOptions, selectedClasses) {
    const classFilters = document.getElementById('splitClassFilters');
    if (!classFilters) return;
    const options = Array.isArray(classOptions) && classOptions.length ? classOptions : classes.map(cls => cls.name);
    if (!options.length) {
        classFilters.innerHTML = '<span class="artifact-missing">暂无类别</span>';
        return;
    }
    const selected = new Set(Array.isArray(selectedClasses) ? selectedClasses : []);
    classFilters.innerHTML = options.map(name => {
        const checked = selected.size === 0 || selected.has(name) ? 'checked' : '';
        return `<label><input type="checkbox" name="splitClasses" value="${escapeHtml(name)}" ${checked}> ${escapeHtml(name)}</label>`;
    }).join('');
}

function buildSplitSummaryHtml(splitData) {
    const counts = splitData.counts || splitData.split_counts || {};
    const totals = splitData.totals || splitData.candidate_totals || {};
    const train = Number(counts.train || 0);
    const val = Number(counts.val || 0);
    const test = Number(counts.test || 0);
    const totalCandidates = Number(totals.total || splitData.total_images || train + val + test || 0);
    const annotated = Number(totals.annotated || splitData.annotated_images || 0);
    return `
        <div>切分：<strong>Train ${train}</strong> / Val ${val} / Test ${test}</div>
        <div>候选样本：${totalCandidates}；已标注：${annotated || '-'}</div>
    `;
}

function getTrainSplitConfigFromInputs() {
    const trainRatio = Number(document.getElementById('splitTrainRatio')?.value || 0);
    const valRatio = Number(document.getElementById('splitValRatio')?.value || 0);
    const testRatio = Number(document.getElementById('splitTestRatio')?.value || 0);
    const total = trainRatio + valRatio + testRatio;
    if (trainRatio <= 0 || valRatio < 0 || testRatio < 0 || Math.abs(total - 1) > 0.001) {
        throw new Error('Train/Val/Test 比例之和必须等于 1，且训练集比例必须大于 0');
    }
    const classFilter = Array.from(document.querySelectorAll('input[name="splitClasses"]:checked')).map(input => input.value);
    return {
        train_ratio: trainRatio,
        val_ratio: valRatio,
        test_ratio: testRatio,
        sample_filter: document.getElementById('splitSampleFilter')?.value || 'annotated',
        class_filter: classFilter
    };
}

function renderTrainReadiness(readiness) {
    const panel = document.getElementById('trainReadiness');
    if (!panel) return;
    const annotated = Number(readiness.annotated_images || 0);
    const total = Number(readiness.total_images || 0);
    const minCount = Number(readiness.min_for_initial || 100);
    const ready = !!readiness.ready_for_initial;
    panel.innerHTML = `
        <div>已标注图片：<strong>${annotated}</strong> / ${total}</div>
        <div>初代模型门槛：${minCount} 张</div>
        <div>初代训练状态：${ready ? '<span style="color:#28a745;">可开始</span>' : '<span style="color:#dc3545;">未达标</span>'}</div>
    `;
    renderCudaStatus(readiness?.cuda || {});
}

function renderCudaStatus(cuda) {
    const panel = document.getElementById('trainCudaStatus');
    if (!panel) return;

    const available = !!cuda.available;
    const deviceName = cuda.device_name || '-';
    const torchVersion = cuda.torch_version || '-';
    const deviceCount = Number(cuda.device_count || 0);
    const error = cuda.error ? `（${escapeHtml(cuda.error)}）` : '';

    panel.innerHTML = available
        ? `CUDA状态：<span style="color:#28a745;font-weight:600;">可用</span> | GPU: <strong>${escapeHtml(deviceName)}</strong> | 数量: ${deviceCount} | Torch: ${escapeHtml(torchVersion)}`
        : `CUDA状态：<span style="color:#dc3545;font-weight:600;">不可用</span> | Torch: ${escapeHtml(torchVersion)} ${error}`;
}

function renderActiveModel(activeModel) {
    const panel = document.getElementById('activeModelInfo');
    if (!panel) return;
    if (!activeModel || !activeModel.model_name) {
        panel.innerHTML = '当前生产模型：<strong>无</strong>';
        return;
    }
    const updated = activeModel.updated_at ? `（${activeModel.updated_at}）` : '';
    panel.innerHTML = `当前生产模型：<strong>${escapeHtml(activeModel.model_name)}</strong> ${escapeHtml(updated)}`;
}

function renderTrainJobs(jobs) {
    const panel = document.getElementById('trainJobsPanel');
    if (!panel) return;
    if (!jobs.length) {
        panel.innerHTML = '<div class="timeline-empty">暂无训练任务</div>';
        return;
    }
    const rows = jobs.slice(0, 15).map((job, idx) => {
        const statusMap = {
            queued: '排队中',
            running: '训练中',
            completed: '完成',
            failed: '失败'
        };
        const statusText = statusMap[job.status] || job.status || 'unknown';
        const percent = Math.max(0, Math.min(100, Number(job.progress || 0)));
        const epoch = job.total_epochs ? `${Number(job.epoch || 0)}/${Number(job.total_epochs || 0)}` : '-';
        return `
            <tr>
                <td>${idx + 1}</td>
                <td>${escapeHtml(job.mode || '')}</td>
                <td><span class="status-badge ${escapeHtml(job.status || '')}">${escapeHtml(statusText)}</span></td>
                <td>${renderTrainProgress(percent)}</td>
                <td>${escapeHtml(epoch)}</td>
                <td>${escapeHtml(formatSplitCounts(job.split_counts))}</td>
                <td title="${escapeHtml(job.message || '')}">${escapeHtml(job.version || job.message || '-')}</td>
                <td>${renderJobArtifactActions(job)}</td>
            </tr>
        `;
    }).join('');
    panel.innerHTML = `
        <table class="train-table train-artifact-table">
            <thead>
                <tr><th>#</th><th>模式</th><th>状态</th><th>进度</th><th>Epoch</th><th>切分</th><th>信息</th><th>操作</th></tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;
}

function renderTrainProgress(percent) {
    return `
        <div class="train-progress-cell">
            <div class="progress-bar-container"><div class="progress-bar" style="width:${percent}%"></div></div>
            <span>${percent}%</span>
        </div>
    `;
}

function renderJobArtifactActions(job) {
    const id = escapeHtml(job.id || '');
    const logDisabled = hasArtifact(job, 'log') || job.status === 'running' || job.status === 'failed' || job.status === 'completed' ? '' : 'disabled';
    const imageDisabled = hasArtifact(job, 'results_png') ? '' : 'disabled';
    const chartDisabled = hasArtifact(job, 'results_csv') ? '' : 'disabled';
    const modelDisabled = hasArtifact(job, 'model') ? '' : 'disabled';
    const csvDisabled = hasArtifact(job, 'results_csv') ? '' : 'disabled';
    return `
        <div class="artifact-actions">
            <button class="btn btn-small btn-secondary" ${logDisabled} onclick="showTrainLogs('${id}')">日志</button>
            <button class="btn btn-small btn-info" ${imageDisabled} onclick="showNativeYoloImages('${id}')">原生图</button>
            <button class="btn btn-small btn-info" ${chartDisabled} onclick="showTrainMetrics('${id}')">CSV曲线</button>
            <button class="btn btn-small btn-success" ${modelDisabled} onclick="downloadTrainArtifact('${id}', 'model')">模型</button>
            <button class="btn btn-small btn-secondary" ${csvDisabled} onclick="downloadTrainArtifact('${id}', 'results_csv')">CSV</button>
        </div>
    `;
}

function renderModelRegistry(models, activeModel) {
    const panel = document.getElementById('modelRegistryPanel');
    if (!panel) return;
    if (!models.length) {
        panel.innerHTML = '<div class="timeline-empty">暂无训练模型</div>';
        return;
    }
    const activeId = activeModel?.model_id || '';
    const rows = models.slice(0, 20).map((model, idx) => {
        const isActive = activeId && activeId === model.id;
        const metrics = model.metrics || {};
        const map50 = Number(metrics['metrics/mAP50(B)'] || 0);
        const precision = Number(metrics['metrics/precision(B)'] || 0);
        const recall = Number(metrics['metrics/recall(B)'] || 0);
        const metricText = (map50 > 0 || precision > 0 || recall > 0)
            ? `P:${precision.toFixed(3)} R:${recall.toFixed(3)} mAP50:${map50.toFixed(3)}`
            : '-';
        return `
            <tr>
                <td>${idx + 1}</td>
                <td>${escapeHtml(model.version || '')}</td>
                <td>${escapeHtml(model.name || '')}</td>
                <td>${escapeHtml(model.mode || '')}</td>
                <td>${escapeHtml(formatSplitCounts(model.split_counts))}</td>
                <td>${escapeHtml(metricText)}</td>
                <td>${renderModelArtifactActions(model, isActive)}</td>
            </tr>
        `;
    }).join('');
    panel.innerHTML = `
        <table class="train-table train-artifact-table">
            <thead>
                <tr><th>#</th><th>版本</th><th>文件</th><th>训练模式</th><th>切分</th><th>指标</th><th>操作</th></tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;
}

function renderModelArtifactActions(model, isActive) {
    const modelId = escapeHtml(model.id || '');
    const jobId = escapeHtml(model.job_id || '');
    const imageDisabled = jobId && hasArtifact(model, 'results_png') ? '' : 'disabled';
    const chartDisabled = jobId && hasArtifact(model, 'results_csv') ? '' : 'disabled';
    const modelDisabled = jobId && hasArtifact(model, 'model') ? '' : 'disabled';
    return `
        <div class="artifact-actions">
            ${isActive ? '<span class="status-badge production">生产中</span>' : `<button class="btn btn-small btn-primary" onclick="activateModel('${modelId}')">设为生产</button>`}
            <button class="btn btn-small btn-info" ${imageDisabled} onclick="showNativeYoloImages('${jobId}')">原生图</button>
            <button class="btn btn-small btn-info" ${chartDisabled} onclick="showTrainMetrics('${jobId}')">CSV曲线</button>
            <button class="btn btn-small btn-success" ${modelDisabled} onclick="downloadTrainArtifact('${jobId}', 'model')">模型</button>
        </div>
    `;
}

function startTraining(mode) {
    if (mode === 'initial') {
        const annotated = (window.allImages || []).filter(img => Number(img.annotation_count || 0) > 0).length;
        if (annotated < COLD_START_MIN_ANNOTATED) {
            showToast(`请至少标注 ${COLD_START_MIN_ANNOTATED} 张图片后再开始训练（当前 ${annotated} 张）`);
            return;
        }
    }

    const epochs = Number(document.getElementById('trainEpochs')?.value || 30);
    const imgsz = Number(document.getElementById('trainImgsz')?.value || 640);
    const batch = Number(document.getElementById('trainBatch')?.value || 8);
    let splitConfig = null;
    try {
        splitConfig = getTrainSplitConfigFromInputs();
    } catch (error) {
        showToast(error.message);
        return;
    }

    fetch('/api/train/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            mode,
            epochs,
            imgsz,
            batch,
            device: 'auto',
            split_config: splitConfig
        })
    })
        .then(async response => {
            const data = await response.json();
            if (!response.ok || data.error) {
                throw new Error(data.error || '启动训练失败');
            }
            showToast(`训练任务已启动：${data.job?.id || ''}`);
            loadTrainingCenter();
        })
        .catch(error => {
            console.error('启动训练失败:', error);
            showToast('启动训练失败: ' + error.message);
        });
}

function saveTrainSplit() {
    let splitConfig = null;
    try {
        splitConfig = getTrainSplitConfigFromInputs();
    } catch (error) {
        showToast(error.message);
        return;
    }

    fetchOptionalJson('/api/train/split', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(splitConfig)
    }).then(data => {
        if (data.unavailable) {
            showToast(`保存切分失败: ${data.error || '接口未就绪'}`);
            renderTrainSplit(data);
            return;
        }
        showToast('训练切分已保存');
        renderTrainSplit(data);
    });
}

function resetTrainSplit() {
    fetchOptionalJson('/api/train/split/reset', { method: 'POST' }).then(data => {
        if (data.unavailable) {
            showToast(`重置切分失败: ${data.error || '接口未就绪'}`);
            renderTrainSplit(data);
            return;
        }
        showToast('训练切分已重置');
        renderTrainSplit(data);
    });
}

function showTrainLogs(jobId) {
    openTrainArtifactModal('训练日志', '<div class="training-log">日志加载中...</div>');
    fetchOptionalJson(`/api/train/jobs/${encodeURIComponent(jobId)}/logs`)
        .then(data => {
            const text = data.log || data.logs || data.text || '';
            const body = text ? escapeHtml(text) : '<span class="artifact-missing">日志不存在或已清理</span>';
            setTrainArtifactBody(`<div class="training-log">${body}</div>`);
        });
}

function showTrainResultsImage(jobId) {
    const url = `/api/train/jobs/${encodeURIComponent(jobId)}/results-image`;
    openTrainArtifactModal('训练结果图', `<div class="train-image-wrap"><img src="${url}" alt="训练结果图" onerror="this.closest('.train-image-wrap').innerHTML='<span class=&quot;artifact-missing&quot;>results.png 不存在或接口未就绪</span>'"></div>`);
}

function showNativeYoloImages(jobId) {
    openTrainArtifactModal('YOLO原生训练图', '<div class="timeline-summary">原生训练图加载中...</div>');
    fetchOptionalJson(`/api/train/jobs/${encodeURIComponent(jobId)}/native-images`)
        .then(data => {
            if (!data || data.unavailable || !Array.isArray(data.images) || !data.images.length) {
                setTrainArtifactBody('<div class="timeline-summary artifact-missing">暂未找到 YOLO 原生训练图，训练完成后会生成 results.png、混淆矩阵、PR/F1 曲线和 batch 预览图。</div>');
                return;
            }
            const cards = data.images.map(image => {
                const name = escapeHtml(image.name || '');
                const title = escapeHtml(image.title || image.name || '训练图');
                const src = `/api/train/jobs/${encodeURIComponent(jobId)}/native-images/${encodeURIComponent(image.name || '')}`;
                return `
                    <figure class="native-yolo-card">
                        <figcaption>${title}<span>${name}</span></figcaption>
                        <a href="${src}" target="_blank" rel="noopener noreferrer">
                            <img src="${src}" alt="${title}" loading="lazy" onerror="this.closest('.native-yolo-card').classList.add('image-missing')">
                        </a>
                    </figure>
                `;
            }).join('');
            setTrainArtifactBody(`<div class="native-yolo-grid">${cards}</div>`);
        });
}

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

function downloadTrainArtifact(jobId, artifact) {
    window.location.href = `/api/train/jobs/${encodeURIComponent(jobId)}/download/${encodeURIComponent(artifact)}`;
}

function openTrainArtifactModal(title, bodyHtml) {
    const modal = document.getElementById('trainArtifactModal');
    const titleEl = document.getElementById('trainArtifactTitle');
    if (titleEl) titleEl.textContent = title;
    setTrainArtifactBody(bodyHtml);
    if (modal) modal.style.display = 'block';
}

function setTrainArtifactBody(bodyHtml) {
    const body = document.getElementById('trainArtifactBody');
    if (body) body.innerHTML = bodyHtml;
}

function activateModel(modelId) {
    fetch(`/api/models/${encodeURIComponent(modelId)}/activate`, {
        method: 'POST'
    })
        .then(async response => {
            const data = await response.json();
            if (!response.ok || data.error) {
                throw new Error(data.error || '切换模型失败');
            }
            showToast('生产模型已切换');
            loadTrainingCenter();
        })
        .catch(error => {
            console.error('切换生产模型失败:', error);
            showToast('切换生产模型失败: ' + error.message);
        });
}

function startTrainPollingIfNeeded(jobs) {
    const hasRunning = (jobs || []).some(job => job.status === 'queued' || job.status === 'running');
    if (!hasRunning) {
        stopTrainPolling();
        return;
    }
    if (trainCenterPolling) return;
    const tick = () => {
        fetch('/api/train/jobs')
            .then(response => response.json())
            .then(data => {
                const allJobs = data.jobs || [];
                renderTrainJobs(allJobs);
                const stillRunning = allJobs.some(job => job.status === 'queued' || job.status === 'running');
                if (stillRunning) {
                    trainCenterPolling = setTimeout(tick, 3000);
                } else {
                    trainCenterPolling = null;
                    loadTrainingCenter();
                }
            })
            .catch(() => {
                trainCenterPolling = setTimeout(tick, 5000);
            });
    };
    trainCenterPolling = setTimeout(tick, 2000);
}

function stopTrainPolling() {
    if (trainCenterPolling) {
        clearTimeout(trainCenterPolling);
        trainCenterPolling = null;
    }
}

function syncAiModelWithActive(activeModel) {
    const activeModelName =
        activeModel?.model_name ||
        ((activeModel?.model_path || '').split(/[\\/]/).pop() || '');
    if (!activeModelName) return;
    aiAnnotateModel = activeModelName;
    const select = document.getElementById('aiModelSelect');
    if (select) {
        const option = Array.from(select.options).find(x => x.value === activeModelName);
        if (option) select.value = activeModelName;
    }
}


// ==================== SOP timeline annotation ====================
