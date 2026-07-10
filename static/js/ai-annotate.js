// AI标注：编排入口 + 模态框 + SAM3/YOLO11 引擎切换 + 模型加载 + 事件绑定
// classic-script 全局作用域；依赖 state.js 中的全局变量与 utils.js 的 showToast 等。
// 单张推理执行见 ai_annotate_run.js；批量推理见 ai_annotate_batch.js（均通过全局函数调用协作）。

function toggleAiAnnotate() {
    if (aiAnnotateEnabled) {
        // 关闭AI标注
        disableAiAnnotate();
    } else {
        // 显示AI标注设置模态框
        showAiAnnotateModal();
    }
}

// 显示AI标注模态框
function showAiAnnotateModal(preferredEngine = null) {
    const modal = document.getElementById('aiAnnotateModal');
    modal.style.display = 'block';

    if (preferredEngine === 'sam3' || preferredEngine === 'yolo11') {
        aiAnnotateEngine = preferredEngine;
    }

    const engineSelect = document.getElementById('aiEngineSelect');
    if (engineSelect) {
        engineSelect.value = aiAnnotateEngine;
    }
    syncWorldClassesInputFromCurrent();
    onAiEngineChanged();

    // 加载可用模型列表
    loadAiModels();

    // 更新批量标注范围信息
    updateBatchRangeInfo();

    // 设置默认范围为全部图片
    const totalImages = window.allImages ? window.allImages.length : 0;
    const endInput = document.getElementById('batchEndIndex');
    if (endInput && totalImages > 0) {
        endInput.value = totalImages;
    }
    updateBatchRangeInfo();
}

function getRecommendedAiEngineForCurrentStep() {
    const nextBtn = document.getElementById('workflowNextBtn');
    const currentStep = Number(workflowSelectedStep || nextBtn?.dataset?.step || 0);
    if (currentStep === 3) return 'sam3';
    return 'yolo11';
}

function syncWorldClassesInputFromCurrent() {
    const input = document.getElementById('worldClassesInput');
    if (!input || input.value.trim()) return;
    if (Array.isArray(classes) && classes.length > 0) {
        input.value = classes.map(c => c.name).filter(Boolean).join(',');
    }
}

function getWorldClassesInput() {
    const input = document.getElementById('worldClassesInput');
    const raw = (input?.value || '').trim();
    if (raw) {
        return raw
            .replace(/，/g, ',')
            .split(',')
            .map(x => x.trim())
            .filter(Boolean);
    }
    return (classes || []).map(c => c.name).filter(Boolean);
}

function onAiEngineChanged() {
    const engineSelect = document.getElementById('aiEngineSelect');
    aiAnnotateEngine = engineSelect?.value || 'yolo11';
    const worldGroup = document.getElementById('worldClassesGroup');
    if (worldGroup) {
        worldGroup.style.display = aiAnnotateEngine === 'sam3' ? 'block' : 'none';
    }
    const startBtn = document.getElementById('aiAnnotateStartBtn');
    if (startBtn) {
        if (aiAnnotateEngine === 'sam3') {
            startBtn.innerHTML = '<i class="fas fa-magic"></i> 开始区间预标注';
        } else {
            startBtn.innerHTML = '<i class="fas fa-play"></i> 开启AI标注';
        }
    }
    loadAiModels();
}

// 加载AI模型列表
function loadAiModels() {
    const installPath = document.getElementById('yolo11InstallPath')?.value || 'plugins/yolo11';
    const modelSelect = document.getElementById('aiModelSelect');

    modelSelect.innerHTML = '<option value="">-- 加载中... --</option>';

    if (aiAnnotateEngine === 'sam3') {
        fetch('/api/sam3/status')
            .then(r => r.json())
            .then(status => {
                modelSelect.innerHTML = '';
                if (status.loaded) {
                    const option = document.createElement('option');
                    option.value = 'sam3';
                    option.textContent = 'SAM3 (' + (status.model_path || '').split(/[\\/]/).pop() + ')';
                    modelSelect.appendChild(option);
                    modelSelect.value = 'sam3';
                    aiAnnotateModel = 'sam3';
                } else {
                    modelSelect.innerHTML = '<option value="">-- SAM3模型未加载 --</option>';
                    showToast('SAM3模型未加载，请检查模型文件路径');
                }
            })
            .catch(() => {
                modelSelect.innerHTML = '<option value="">-- SAM3状态检查失败 --</option>';
            });
        return;
    }

    Promise.all([
        fetch(`/api/list-models?install_path=${encodeURIComponent(installPath)}`).then(response => response.json()),
        fetch('/api/models/active').then(response => response.json()).catch(() => ({}))
    ])
        .then(([modelsData, activeModel]) => {
            modelSelect.innerHTML = '<option value="">-- 请选择模型 --</option>';
            if (modelsData.models && modelsData.models.length > 0) {
                modelsData.models.forEach(model => {
                    const option = document.createElement('option');
                    option.value = model;
                    option.textContent = model;
                    modelSelect.appendChild(option);
                });

                // 优先使用当前会话已选择模型，其次用激活模型
                const preferredModel =
                    aiAnnotateModel ||
                    activeModel?.model_name ||
                    ((activeModel?.model_path || '').split(/[\\/]/).pop() || '');
                if (preferredModel && modelsData.models.includes(preferredModel)) {
                    modelSelect.value = preferredModel;
                    aiAnnotateModel = preferredModel;
                }
            } else {
                modelSelect.innerHTML = '<option value="">-- 无可用模型，请去设置里安装模型 --</option>';
                showToast('当前无可用模型，正在为你打开“设置 -> YOLO11 模型管理”');
                openSettingsToModelInstall();
            }
        })
        .catch(error => {
            console.error('加载模型列表失败:', error);
            modelSelect.innerHTML = '<option value="">-- 加载失败，请去设置里安装模型 --</option>';
            showToast('模型列表加载失败，正在为你打开“设置 -> YOLO11 模型管理”');
            openSettingsToModelInstall();
        });
}

// 设置AI标注事件
function setupAiAnnotateEvents() {
    const engineSelect = document.getElementById('aiEngineSelect');
    if (engineSelect) {
        engineSelect.addEventListener('change', onAiEngineChanged);
    }

    // 置信度滑块
    const confidenceSlider = document.getElementById('aiConfidence');
    const confidenceValue = document.getElementById('aiConfidenceValue');
    if (confidenceSlider && confidenceValue) {
        confidenceSlider.addEventListener('input', function() {
            confidenceValue.textContent = this.value;
        });
    }

    // AI标注表单提交
    const aiForm = document.getElementById('aiAnnotateForm');
    if (aiForm) {
        aiForm.addEventListener('submit', function(e) {
            e.preventDefault();
            enableAiAnnotate();
        });
    }

    // 取消按钮
    const cancelBtn = document.getElementById('aiAnnotateCancelBtn');
    if (cancelBtn) {
        cancelBtn.addEventListener('click', function() {
            document.getElementById('aiAnnotateModal').style.display = 'none';
        });
    }

    // 批量标注按钮
    const batchBtn = document.getElementById('aiBatchAnnotateBtn');
    if (batchBtn) {
        batchBtn.addEventListener('click', function() {
            startBatchAnnotate();
        });
    }

    // 批量标注取消按钮
    const batchCancelBtn = document.getElementById('batchCancelBtn');
    if (batchCancelBtn) {
        batchCancelBtn.addEventListener('click', function() {
            cancelBatchAnnotate();
        });
    }

    // 批量标注范围输入框事件
    const startIndexInput = document.getElementById('batchStartIndex');
    const endIndexInput = document.getElementById('batchEndIndex');
    if (startIndexInput) {
        startIndexInput.addEventListener('input', updateBatchRangeInfo);
    }
    if (endIndexInput) {
        endIndexInput.addEventListener('input', updateBatchRangeInfo);
    }

    if (engineSelect) {
        onAiEngineChanged();
    }
}
