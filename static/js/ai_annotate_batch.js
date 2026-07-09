// AI标注：批量推理（区间/分批/取消/范围配置）
// classic-script 全局作用域；依赖 state.js 全局变量与 utils.js 的 showToast 等。

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
