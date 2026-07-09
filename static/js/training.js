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
