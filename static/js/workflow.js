function updateWorkflowGuide() {
    const hintEl = document.getElementById('workflowHint');
    const goalEl = document.getElementById('workflowGoal');
    const dodEl = document.getElementById('workflowDod');
    const stepIds = ['wfStep1', 'wfStep2', 'wfStep3', 'wfStep4', 'wfStep5', 'wfStep6'];
    if (!hintEl || !goalEl || !dodEl || !document.getElementById(stepIds[0])) return;

    const images = window.allImages || [];
    const total = images.length;
    const annotated = images.filter(img => img.annotation_count > 0).length;
    const hasClasses = Array.isArray(classes) && classes.length > 0;

    stepIds.forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.classList.remove('active');
        el.classList.remove('done');
    });

    const step1 = document.getElementById('wfStep1');
    const step2 = document.getElementById('wfStep2');
    const step3 = document.getElementById('wfStep3');
    const step4 = document.getElementById('wfStep4');
    const step5 = document.getElementById('wfStep5');
    const step6 = document.getElementById('wfStep6');

    if (total > 0) step1.classList.add('done');
    if (hasClasses) step2.classList.add('done');
    if (annotated >= COLD_START_MIN_ANNOTATED) {
        step3.classList.add('done');
        step4.classList.add('done');
    }
    if (annotated >= COLD_START_MIN_ANNOTATED) step5.classList.add('active');
    if (annotated >= 150) step6.classList.add('active');

    let suggestedStep = 1;
    if (total === 0) {
        step1.classList.add('active');
        hintEl.textContent = '当前建议：先导入视频/图片并抽帧生成样本';
        suggestedStep = 1;
    } else if (!hasClasses) {
        step2.classList.add('active');
        hintEl.textContent = '当前建议：先创建标签体系，再开始标注';
        suggestedStep = 2;
    } else if (annotated < COLD_START_MIN_ANNOTATED) {
        step3.classList.add('active');
        hintEl.textContent = `当前建议：先用 SAM3 批量预标，再人工兜底（已 ${annotated}/${COLD_START_MIN_ANNOTATED}）`;
        suggestedStep = 3;
    } else if (annotated < 150) {
        step5.classList.add('active');
        hintEl.textContent = `当前建议：可用 v1.0 进行AI标注并人工复核（已标注 ${annotated} 张）`;
        suggestedStep = 5;
    } else {
        step6.classList.add('active');
        hintEl.textContent = '当前建议：进入增量训练并导出稳定数据集版本';
        suggestedStep = 6;
    }

    const focusStep = workflowSelectedStep || suggestedStep;
    renderWorkflowStepDetail(focusStep, {
        total,
        annotated,
        hasClasses
    });
    applyStepSpecificLayout(focusStep);
}

function setupWorkflowStepClickEvents() {
    const stepIds = ['wfStep1', 'wfStep2', 'wfStep3', 'wfStep4', 'wfStep5', 'wfStep6'];
    stepIds.forEach((stepId, idx) => {
        const el = document.getElementById(stepId);
        if (!el) return;
        el.style.cursor = 'pointer';
        el.addEventListener('click', () => {
            workflowSelectedStep = idx + 1;
            updateWorkflowGuide();
        });
    });
}

function renderWorkflowStepDetail(step, snapshot) {
    const goalEl = document.getElementById('workflowGoal');
    const dodEl = document.getElementById('workflowDod');
    const nextBtn = document.getElementById('workflowNextBtn');
    if (!goalEl || !dodEl || !nextBtn) return;

    const mapping = {
        1: {
            title: '步骤1：导入数据',
            goal: '目标：导入首批可标注样本',
            dod: '完成标准：图片列表非空',
            actionLabel: '去导入数据',
            taskDesc: '导入视频或图片并抽帧，先把样本池建立起来。',
            checklist: [
                { text: '已导入至少1个视频或图片目录', done: snapshot.total > 0 },
                { text: '图片列表可浏览和搜索', done: snapshot.total > 0 },
                { text: '准备进入标签定义', done: snapshot.total > 0 }
            ]
        },
        2: {
            title: '步骤2：标签规范',
            goal: '目标：建立稳定标签体系',
            dod: `完成标准：至少 1 个标签（当前 ${snapshot.hasClasses ? '已完成' : '未完成'}）`,
            actionLabel: '去设置标签',
            taskDesc: '先定义清晰类别边界，后续AI标注质量才稳定。',
            checklist: [
                { text: '创建至少1个有效标签', done: snapshot.hasClasses },
                { text: '确认标签命名规范统一', done: snapshot.hasClasses },
                { text: '准备进入冷启动标注', done: snapshot.hasClasses }
            ]
        },
        3: {
            title: `步骤3：SAM3预标+人工兜底（${COLD_START_MIN_ANNOTATED}张）`,
            goal: '目标：用 SAM3 快速冷启动并人工兜底',
            dod: `完成标准：已标注 >= ${COLD_START_MIN_ANNOTATED} 张（当前 ${snapshot.annotated}/${COLD_START_MIN_ANNOTATED}）`,
            actionLabel: '去SAM3预标',
            taskDesc: '先用 SAM3 批量预标，再逐张人工复核，保证质量与效率。',
            checklist: [
                { text: 'AI引擎选择为 SAM3 并完成一轮预标', done: snapshot.annotated > 0 },
                { text: `已标注数量达到${COLD_START_MIN_ANNOTATED}张（当前 ${snapshot.annotated}）`, done: snapshot.annotated >= COLD_START_MIN_ANNOTATED },
                { text: '抽检关键类别并人工修正漏标/误标', done: snapshot.annotated >= COLD_START_MIN_ANNOTATED },
                { text: '可启动 v1.0 训练', done: snapshot.annotated >= COLD_START_MIN_ANNOTATED }
            ]
        },
        4: {
            title: '步骤4：训练v1.0',
            goal: '目标：训练第一版业务模型 v1.0',
            dod: '完成标准：训练任务完成且可设为生产模型',
            actionLabel: '去训练中心',
            taskDesc: '在训练中心启动首轮训练，观察任务状态与模型产出。',
            checklist: [
                { text: '打开训练中心并检查准备度', done: false },
                { text: '启动初代训练任务', done: false },
                { text: '任务完成并可用于AI预标', done: false }
            ]
        },
        5: {
            title: '步骤5：AI标注+复核',
            goal: '目标：用业务模型(v1.0+)批量预标并人工复核',
            dod: '完成标准：批量标注执行且样本经人工审查',
            actionLabel: '去AI标注',
            taskDesc: '此阶段优先选择业务模型（YOLO11），持续提效并保持人工兜底。',
            checklist: [
                { text: '已选择业务模型并设置置信度', done: false },
                { text: '已执行批量预标注', done: false },
                { text: '已完成人工复核与修补', done: false }
            ]
        },
        6: {
            title: '步骤6：增量训练与导出',
            goal: '目标：增量训练并导出版本化数据集',
            dod: '完成标准：完成一次增量训练并导出数据',
            actionLabel: '去导出数据',
            taskDesc: '进入稳定迭代，输出可复用的版本化数据资产。',
            checklist: [
                { text: '增量训练任务已启动并完成', done: false },
                { text: '核心类别效果达到预期', done: false },
                { text: '已导出可复现数据集版本', done: false }
            ]
        }
    };

    const conf = mapping[step] || mapping[1];
    goalEl.textContent = conf.goal;
    dodEl.textContent = conf.dod;
    nextBtn.textContent = conf.actionLabel;
    nextBtn.dataset.step = String(step);
    const primaryBtn = document.getElementById('workflowPrimaryActionBtn');
    if (primaryBtn) {
        primaryBtn.textContent = conf.actionLabel;
        primaryBtn.dataset.step = String(step);
    }
    renderWorkflowTaskCard(conf);
    highlightStepButtons(step);
}

function runWorkflowNextAction() {
    const nextBtn = document.getElementById('workflowNextBtn');
    const primaryBtn = document.getElementById('workflowPrimaryActionBtn');
    const step = Number(primaryBtn?.dataset?.step || nextBtn?.dataset?.step || '1');
    switch (step) {
        case 1:
            showDatasetModal();
            break;
        case 2:
            showToast('在右侧“标签管理”中添加标签，至少创建1个');
            break;
        case 3:
            showAiAnnotateModal('sam3');
            showToast('冷启动建议选择 SAM3，先批量预标再人工兜底');
            break;
        case 4:
            showTrainingCenterModal();
            break;
        case 5:
            showAiAnnotateModal('yolo11');
            showToast('当前阶段建议使用业务模型（YOLO11）批量预标并人工复核');
            break;
        case 6:
            showExportModal();
            break;
        default:
            showDatasetModal();
            break;
    }
}

function renderWorkflowTaskCard(conf) {
    const titleEl = document.getElementById('workflowTaskTitle');
    const descEl = document.getElementById('workflowTaskDesc');
    const checklistEl = document.getElementById('workflowChecklist');
    if (!titleEl || !descEl || !checklistEl) return;

    titleEl.textContent = conf.title || '';
    descEl.textContent = conf.taskDesc || '';
    checklistEl.innerHTML = '';
    (conf.checklist || []).forEach(item => {
        const li = document.createElement('li');
        li.textContent = `${item.done ? '✔' : '○'} ${item.text}`;
        if (item.done) li.classList.add('done');
        checklistEl.appendChild(li);
    });
}

function highlightStepButtons(step) {
    const buttonIds = ['openFolderBtn', 'aiAnnotateToggle', 'trainCenterBtn', 'exportBtn', 'saveAnnotationBtn', 'quickImportBtn', 'quickAiBtn', 'quickTrainBtn', 'quickExportBtn'];
    buttonIds.forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.classList.remove('step-focused-btn');
    });
    const highlightMap = {
        1: ['openFolderBtn', 'quickImportBtn'],
        2: [],
        3: ['saveAnnotationBtn'],
        4: ['trainCenterBtn', 'quickTrainBtn'],
        5: ['aiAnnotateToggle', 'quickAiBtn'],
        6: ['exportBtn', 'quickExportBtn']
    };
    (highlightMap[step] || []).forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.classList.add('step-focused-btn');
    });
}

function applyStepSpecificLayout(step) {
    const classSection = document.getElementById('classToolSection');
    const annotationSection = document.getElementById('annotationToolSection');
    if (!classSection || !annotationSection) return;

    classSection.classList.remove('hidden');
    annotationSection.classList.remove('hidden');

    if (step === 1 || step === 4 || step === 6) {
        classSection.classList.add('hidden');
        annotationSection.classList.add('hidden');
    } else if (step === 2) {
        classSection.classList.remove('hidden');
        annotationSection.classList.add('hidden');
    } else {
        classSection.classList.remove('hidden');
        annotationSection.classList.remove('hidden');
    }
}

// 筛选图片
