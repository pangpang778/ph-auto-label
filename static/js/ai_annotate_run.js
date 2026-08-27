// AI标注：单张推理执行与运行状态 UI
// classic-script 全局作用域；依赖 state.js 中的全局变量与 utils.js 的 showToast 等。

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

    if (aiAnnotateEngine === 'vlm') {
        const worldClasses = getWorldClassesInput();
        if (!worldClasses.length) {
            showToast('请先输入至少一个目标类');
            return;
        }
        showToast('大模型(VLM) 将按区间执行批量预标注，逐张推理较慢，请耐心等待...');
        startBatchAnnotate();
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

    statusInfo.innerHTML = `<i class="fas fa-robot"></i> ${aiAnnotateEngine} / ${escapeHtml(aiAnnotateModel)} | 阈值:${aiAnnotateConfidence}`;
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
    // ponytail: capture image identity at fetch start; discard results if user switched images before response lands
    const targetImage = currentImage;

    const installPath = document.getElementById('yolo11InstallPath')?.value || 'plugins/yolo11';
    const worldClasses = (aiAnnotateEngine === 'sam3' || aiAnnotateEngine === 'vlm') ? getWorldClassesInput() : [];
    const endpoint = {'sam3': '/api/ai-annotate-sam3', 'vlm': '/api/ai-annotate-vlm'}[aiAnnotateEngine] || '/api/ai-annotate';

    fetch(endpoint, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            image_name: targetImage,
            model_name: aiAnnotateModel,
            model: aiAnnotateEngine === 'vlm' ? aiAnnotateModel : undefined,
            confidence: aiAnnotateConfidence,
            install_path: installPath,
            device: 'auto',
            world_classes: worldClasses
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            showToast(`AI标注失败: ${data.error}`);
            return;
        }

        // 结果到达时用户已切到别的图 -> 丢弃，避免把A的结果写到C的标注数组
        if (currentImage !== targetImage) {
            showToast('AI标注结果已过期（已切换图片）', 'warning');
            return;
        }

        if (data.annotations && data.annotations.length > 0) {
            // 将AI标注结果添加到当前标注
            data.annotations.forEach(ann => {
                // 生成唯一ID
                ann.id = Date.now() + Math.floor(Math.random() * 1000000000);
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
        console.error('AI标注失败:', error);
        showToast('AI标注失败: ' + error.message, 'error');
    })
    .finally(() => {
        // ponytail: 无论成功/失败都释放守卫，避免一次失败永久阻塞后续标注
        aiAnnotating = false;
    });
}
