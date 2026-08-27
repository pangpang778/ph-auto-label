// 深度蒸馏训练中心：任务类型切换 + 伪标签生成 + 数据集选择（工单 05/06）
'use strict';

function setupDepthTrainingEvents() {
    document.querySelectorAll('input[name="trainTaskType"]').forEach((radio) => {
        radio.addEventListener('change', updateDepthTrainPanels);
    });
    const pseudoBtn = document.getElementById('startPseudoBtn');
    if (pseudoBtn) pseudoBtn.addEventListener('click', startPseudoLabelJob);
    const depthBtn = document.getElementById('startDepthTrainBtn');
    if (depthBtn) depthBtn.addEventListener('click', startDepthTraining);
    const refreshBtn = document.getElementById('refreshPseudoVideosBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', loadPseudoVideoOptions);
}

function updateDepthTrainPanels() {
    const checked = document.querySelector('input[name="trainTaskType"]:checked');
    const isDepth = !!checked && checked.value === 'depth';
    const panel = document.getElementById('depthTrainPanel');
    if (panel) panel.style.display = isDepth ? '' : 'none';
    // 检测专属控件在深度模式隐藏
    const splitCard = document.querySelector('.train-split-card');
    if (splitCard) splitCard.style.display = isDepth ? 'none' : '';
    const detectParamsRow = document.getElementById('trainEpochs')?.closest('.timeline-inline-row');
    if (detectParamsRow) detectParamsRow.style.display = isDepth ? 'none' : '';
    const detectActions = document.getElementById('startInitialTrainBtn')?.parentElement;
    if (detectActions) detectActions.style.display = isDepth ? 'none' : '';
    if (isDepth) {
        loadPseudoVideoOptions();
    }
}

async function loadPseudoVideoOptions() {
    const select = document.getElementById('pseudoVideoSelect');
    if (!select) return;
    try {
        const response = await fetch('/api/video-test/videos');
        const data = await response.json();
        select.innerHTML = '';
        (data.videos || []).forEach((v) => {
            if ((v.name || '').startsWith('ai_')) return; // 跳过推理输出
            const option = document.createElement('option');
            option.value = v.name;
            option.textContent = `${v.name} (${v.source === 'default' ? '默认' : '上传'})`;
            select.appendChild(option);
        });
        if (!select.options.length) {
            select.innerHTML = '<option value="">无可用视频</option>';
        }
    } catch (error) {
        select.innerHTML = '<option value="">加载失败</option>';
    }
}

// 已完成伪标签任务 -> 数据集下拉选项
function refreshDepthDatasetOptions(jobs) {
    const select = document.getElementById('depthDatasetSelect');
    if (!select) return;
    const datasets = (jobs || []).filter((job) => job.task_type === 'pseudo'
        && job.status === 'completed' && job.artifact_path);
    select.innerHTML = '';
    if (!datasets.length) {
        select.innerHTML = '<option value="">暂无已完成数据集（先完成伪标签生成）</option>';
        return;
    }
    datasets.forEach((job) => {
        const option = document.createElement('option');
        option.value = job.artifact_path;
        option.textContent = `${(job.id || '').slice(-6)} · ${job.split_counts?.frames ?? '?'}帧 · ${job.message || ''}`;
        select.appendChild(option);
    });
}

function startPseudoLabelJob() {
    const select = document.getElementById('pseudoVideoSelect');
    const videos = select ? Array.from(select.selectedOptions).map((o) => o.value).filter(Boolean) : [];
    if (!videos.length) {
        showToast('请选择至少一个视频');
        return;
    }
    const interval = Number(document.getElementById('pseudoInterval')?.value || 0.2);
    fetch('/api/train/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_type: 'pseudo', videos, interval_s: interval })
    })
        .then(async (response) => {
            const data = await response.json();
            if (!response.ok || data.error) throw new Error(data.error || '启动失败');
            showToast(`伪标签生成已启动：${data.job?.id || ''}`);
            loadTrainingCenter();
        })
        .catch((error) => showToast('伪标签启动失败: ' + error.message));
}

function startDepthTraining() {
    const datasetDir = document.getElementById('depthDatasetSelect')?.value;
    if (!datasetDir) {
        showToast('请选择已完成的伪标签数据集');
        return;
    }
    const epochs = Number(document.getElementById('depthEpochs')?.value || 50);
    fetch('/api/train/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_type: 'depth', dataset_dir: datasetDir, epochs, device: 'auto' })
    })
        .then(async (response) => {
            const data = await response.json();
            if (!response.ok || data.error) throw new Error(data.error || '启动失败');
            showToast(`深度蒸馏已启动：${data.job?.id || ''}`);
            loadTrainingCenter();
        })
        .catch((error) => showToast('深度蒸馏启动失败: ' + error.message));
}
