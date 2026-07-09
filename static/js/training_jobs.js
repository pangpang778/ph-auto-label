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
