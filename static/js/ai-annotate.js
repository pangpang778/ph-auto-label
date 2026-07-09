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

// 开启AI标注
function enableAiAnnotate() {
    const engineSelect = document.getElementById('aiEngineSelect');
    const modelSelect = document.getElementById('aiModelSelect');
    const confidenceSlider = document.getElementById('aiConfidence');
    const autoNextCheckbox = document.getElementById('aiAutoNext');
    aiAnnotateEngine = engineSelect?.value || 'yolo11';
    
    if (!modelSelect.value) {
        showToast('无可用模型，请去“设置 -> YOLO11 模型管理”安装模型');
        openSettingsToModelInstall();
        return;
    }

    if (aiAnnotateEngine === 'sam3') {
        const worldClasses = getWorldClassesInput();
        if (!worldClasses.length) {
            showToast('请先输入至少一个目标类（如 base,frame,mirror,screw）');
            return;
        }
        // SAM3 默认走批量预标注（按区间），避免用户误以为”开启AI标注”会自动处理多张
        showToast('SAM3 将按你设置的区间执行批量预标注，请稍候...');
        startBatchAnnotate();
        return;
    }
    
    // 保存设置
    aiAnnotateModel = modelSelect.value;
    aiAnnotateConfidence = parseFloat(confidenceSlider.value);
    aiAutoNext = autoNextCheckbox.checked;
    if (aiAutoNext) {
        const range = getAiRangeConfig();
        aiAutoRangeStart = range.start;
        aiAutoRangeEnd = range.end;
    } else {
        aiAutoRangeStart = null;
        aiAutoRangeEnd = null;
    }
    aiAnnotateEnabled = true;
    
    // 关闭模态框
    document.getElementById('aiAnnotateModal').style.display = 'none';
    
    // 更新按钮状态
    updateAiAnnotateButton();
    
    // 显示状态栏
    showAiStatusBar();
    
    showToast(`AI标注已开启，${aiAnnotateEngine} / 模型: ${aiAnnotateModel}`);
    
    // 自动模式下，从配置的起始图开始
    if (aiAutoNext && window.allImages && window.allImages.length > 0 && Number.isFinite(aiAutoRangeStart)) {
        const startIndex = Math.max(0, aiAutoRangeStart - 1);
        const startImage = window.allImages[startIndex];
        if (startImage && currentImage !== startImage.name) {
            selectImage(startImage.name);
            return;
        }
    }

    // 如果当前有图片，立即进行AI标注
    if (currentImage) {
        performAiAnnotate();
    }
}

// 关闭AI标注
function disableAiAnnotate() {
    aiAnnotateEnabled = false;
    
    // 更新按钮状态
    updateAiAnnotateButton();
    
    // 隐藏状态栏
    hideAiStatusBar();
    
    showToast('AI标注已关闭');
}

// 更新AI标注按钮状态
function updateAiAnnotateButton() {
    const btn = document.getElementById('aiAnnotateToggle');
    if (aiAnnotateEnabled) {
        btn.classList.add('ai-active');
        btn.innerHTML = '<i class="fas fa-robot"></i> AI标注中';
        btn.style.backgroundColor = '#28a745';
        btn.style.borderColor = '#28a745';
    } else {
        btn.classList.remove('ai-active');
        btn.innerHTML = '<i class="fas fa-robot"></i> AI标注';
        btn.style.backgroundColor = '';
        btn.style.borderColor = '';
    }
}

// 显示AI状态栏 - 在导航栏内显示
function showAiStatusBar() {
    // 检查是否已存在状态信息
    let statusInfo = document.getElementById('aiStatusInfo');
    if (!statusInfo) {
        statusInfo = document.createElement('span');
        statusInfo.id = 'aiStatusInfo';
        statusInfo.className = 'ai-status-info';
        
        // 插入到导航栏logo后面
        const logo = document.querySelector('.logo');
        if (logo) {
            logo.parentNode.insertBefore(statusInfo, logo.nextSibling);
        }
    }
    
    statusInfo.innerHTML = `<i class="fas fa-robot"></i> ${aiAnnotateEngine} / ${aiAnnotateModel} | 阈值:${aiAnnotateConfidence}`;
    statusInfo.style.display = 'inline-flex';
}

// 隐藏AI状态栏
function hideAiStatusBar() {
    const statusInfo = document.getElementById('aiStatusInfo');
    if (statusInfo) {
        statusInfo.style.display = 'none';
    }
}

// 执行AI标注
function performAiAnnotate() {
    if (!aiAnnotateEnabled || !currentImage || aiAnnotating) {
        return;
    }
    
    aiAnnotating = true;
    showToast('正在进行AI标注...');
    
    const installPath = document.getElementById('yolo11InstallPath')?.value || 'plugins/yolo11';
    const worldClasses = aiAnnotateEngine === 'sam3' ? getWorldClassesInput() : [];
    const endpoint = aiAnnotateEngine === 'sam3' ? '/api/ai-annotate-sam3' : '/api/ai-annotate';
    
    fetch(endpoint, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            image_name: currentImage,
            model_name: aiAnnotateModel,
            confidence: aiAnnotateConfidence,
            install_path: installPath,
            device: 'auto',
            world_classes: worldClasses
        })
    })
    .then(response => response.json())
    .then(data => {
        aiAnnotating = false;
        
        if (data.error) {
            showToast(`AI标注失败: ${data.error}`);
            return;
        }
        
        if (data.annotations && data.annotations.length > 0) {
            // 将AI标注结果添加到当前标注
            data.annotations.forEach(ann => {
                // 生成唯一ID
                ann.id = Date.now() + Math.floor(Math.random() * 1000);
                currentAnnotations.push(ann);
            });
            
            updateAnnotationListDebounced();
            redrawCanvas();
            
            showToast(`AI标注完成，检测到 ${data.annotations.length} 个目标`);
            
            // 如果有新类别被添加，刷新类别列表
            if (data.new_classes_added) {
                loadClasses();
            }
        } else {
            showToast('AI标注完成，未检测到目标');
        }
    })
    .catch(error => {
        aiAnnotating = false;
        console.error('AI标注失败:', error);
        showToast('AI标注失败: ' + error.message);
    });
}

// 切换到下一张图片
function updateBatchRangeInfo() {
    const totalImages = window.allImages ? window.allImages.length : 0;
    const startInput = document.getElementById('batchStartIndex');
    const endInput = document.getElementById('batchEndIndex');
    const infoText = document.getElementById('batchRangeInfo');
    
    if (startInput && endInput && infoText) {
        // 设置最大值
        startInput.max = totalImages;
        endInput.max = totalImages;
        
        // 如果结束值大于总数，调整为总数
        if (parseInt(endInput.value) > totalImages) {
            endInput.value = totalImages;
        }
        
        const start = parseInt(startInput.value) || 1;
        const end = parseInt(endInput.value) || totalImages;
        const rangeCount = Math.max(0, end - start + 1);
        
        infoText.textContent = `共 ${totalImages} 张图片，当前选择范围: ${start}-${end} (${rangeCount}张)`;
    }
}

// 开始批量标注 - 使用新的批量API
async function startBatchAnnotate() {
    const engineSelect = document.getElementById('aiEngineSelect');
    const modelSelect = document.getElementById('aiModelSelect');
    const confidenceSlider = document.getElementById('aiConfidence');
    const skipAnnotatedCheckbox = document.getElementById('aiSkipAnnotated');
    const batchSizeSelect = document.getElementById('batchSize');
    const startIndexInput = document.getElementById('batchStartIndex');
    const endIndexInput = document.getElementById('batchEndIndex');
    
    if (!modelSelect.value) {
        showToast('请选择一个模型');
        return;
    }
    
    if (!window.allImages || window.allImages.length === 0) {
        showToast('没有图片可以标注');
        return;
    }
    
    // 获取设置
    const engine = engineSelect?.value || aiAnnotateEngine || 'yolo11';
    aiAnnotateEngine = engine;
    const model = modelSelect.value;
    const confidence = parseFloat(confidenceSlider.value);
    const skipAnnotated = skipAnnotatedCheckbox ? skipAnnotatedCheckbox.checked : false;
    const batchSizeRaw = parseInt(batchSizeSelect?.value || '10');
    const batchSize = Number.isFinite(batchSizeRaw) ? batchSizeRaw : 10;
    if (batchSize > 50) {
        showToast('单批最大限制为 50 张，请调整后重试');
        if (batchSizeSelect) batchSizeSelect.value = '50';
        return;
    }
    if (batchSize < 1) {
        showToast('批量大小必须大于 0');
        return;
    }
    const installPath = document.getElementById('yolo11InstallPath')?.value || 'plugins/yolo11';
    const worldClasses = engine === 'sam3' ? getWorldClassesInput() : [];

    if (engine === 'sam3' && worldClasses.length === 0) {
        showToast('请先输入 SAM3 的目标类（如 base,frame,mirror,screw）');
        return;
    }
    
    // 获取区间范围
    const startIndex = Math.max(1, parseInt(startIndexInput?.value || '1'));
    const endIndex = Math.min(window.allImages.length, parseInt(endIndexInput?.value || window.allImages.length));
    
    if (startIndex > endIndex) {
        showToast('起始位置不能大于结束位置');
        return;
    }
    
    // 获取指定范围内的图片
    let imagesToAnnotate = window.allImages.slice(startIndex - 1, endIndex);
    
    // 过滤已标注的图片
    if (skipAnnotated) {
        imagesToAnnotate = imagesToAnnotate.filter(img => img.annotation_count === 0);
    }
    
    if (imagesToAnnotate.length === 0) {
        showToast('选定范围内没有需要标注的图片');
        return;
    }
    
    // 显示进度条
    const progressDiv = document.getElementById('batchAnnotateProgress');
    const progressText = document.getElementById('batchProgressText');
    const progressBar = document.getElementById('batchProgressBar');
    const resultText = document.getElementById('batchResultText');
    
    progressDiv.style.display = 'block';
    progressBar.style.width = '0%';
    resultText.innerHTML = '';
    
    // 禁用按钮
    document.getElementById('aiBatchAnnotateBtn').disabled = true;
    document.getElementById('aiAnnotateStartBtn').disabled = true;
    
    batchAnnotateRunning = true;
    batchAnnotateCancelled = false;
    
    let successCount = 0;
    let failCount = 0;
    let totalDetected = 0;
    const total = imagesToAnnotate.length;
    
    showToast(`开始批量标注 ${total} 张图片，每批 ${batchSize} 张...`);
    
    // 分批处理
    const batches = [];
    for (let i = 0; i < imagesToAnnotate.length; i += batchSize) {
        batches.push(imagesToAnnotate.slice(i, i + batchSize));
    }
    
    let processedCount = 0;
    
    for (let batchIndex = 0; batchIndex < batches.length; batchIndex++) {
        if (batchAnnotateCancelled) {
            resultText.innerHTML += `<div style="color: orange;">已取消，共处理 ${processedCount} 张图片</div>`;
            break;
        }
        
        const batch = batches[batchIndex];
        const batchImageNames = batch.map(img => img.name);
        
        progressText.textContent = `正在处理第 ${batchIndex + 1}/${batches.length} 批 (${processedCount + 1}-${processedCount + batch.length}/${total})`;
        progressBar.style.width = `${((processedCount + batch.length) / total) * 100}%`;
        
        try {
            // 使用批量API
            const endpoint = engine === 'sam3' ? '/api/ai-annotate-sam3-batch' : '/api/ai-annotate-batch';
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    image_names: batchImageNames,
                    model_name: model,
                    confidence: confidence,
                    install_path: installPath,
                    device: 'auto',
                    world_classes: worldClasses
                })
            });
            
            const data = await response.json();
            
            if (data.error) {
                failCount += batch.length;
                resultText.innerHTML += `<div style="color: red;">✗ 批次 ${batchIndex + 1} 失败: ${data.error}</div>`;
            } else if (data.results) {
                // 处理每张图片的结果
                for (const result of data.results) {
                    if (result.success) {
                        if (result.count > 0) {
                            successCount++;
                            totalDetected += result.count;
                            resultText.innerHTML += `<div style="color: green;">✓ ${result.image_name}: ${result.count} 个目标</div>`;
                        } else {
                            resultText.innerHTML += `<div style="color: gray;">○ ${result.image_name}: 无目标</div>`;
                        }
                    } else {
                        failCount++;
                        resultText.innerHTML += `<div style="color: red;">✗ ${result.image_name}: 失败</div>`;
                    }
                }
            }
            
            // 滚动到底部
            resultText.scrollTop = resultText.scrollHeight;
            
        } catch (error) {
            failCount += batch.length;
            resultText.innerHTML += `<div style="color: red;">✗ 批次 ${batchIndex + 1} 错误: ${error.message}</div>`;
        }
        
        processedCount += batch.length;
    }
    
    // 完成
    batchAnnotateRunning = false;
    progressText.textContent = `完成: 处理 ${processedCount} 张，检测到 ${totalDetected} 个目标`;
    progressBar.style.width = '100%';
    
    // 恢复按钮
    document.getElementById('aiBatchAnnotateBtn').disabled = false;
    document.getElementById('aiAnnotateStartBtn').disabled = false;
    
    // 刷新图片列表和类别
    loadImages();
    loadClasses();
    
    showToast(`批量标注完成！处理 ${processedCount} 张图片，检测到 ${totalDetected} 个目标`);
}

// 取消批量标注
function cancelBatchAnnotate() {
    if (batchAnnotateRunning) {
        batchAnnotateCancelled = true;
        showToast('正在取消批量标注...');
    }
}

function getAiRangeConfig() {
    const totalImages = window.allImages ? window.allImages.length : 0;
    const startInput = document.getElementById('batchStartIndex');
    const endInput = document.getElementById('batchEndIndex');
    let start = parseInt(startInput?.value || '1', 10);
    let end = parseInt(endInput?.value || String(totalImages || 1), 10);
    if (!Number.isFinite(start) || start < 1) start = 1;
    if (!Number.isFinite(end) || end < start) end = start;
    if (totalImages > 0) {
        start = Math.min(start, totalImages);
        end = Math.min(end, totalImages);
    }
    return { start, end, total: totalImages };
}

// ==================== 训练中心 ====================
