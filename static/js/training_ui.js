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
            if (typeof refreshDepthDatasetOptions === 'function') {
                refreshDepthDatasetOptions(jobsData.jobs || []);
            }
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
